import asyncio
import json
import uuid
import time
import httpx
import logging
import os
import redis.asyncio as redis
from fastapi import FastAPI, Response, Request
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("api_proxy")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
SECONDS_BETWEEN_REQUESTS = float(os.getenv("RATE_LIMIT_DELAY", "3.0"))
REDIS_QUEUE_KEY = "api_request_queue"
REDIS_JOB_PREFIX = "job:"
REDIS_EXPIRATION = 3600
GLOBAL_HEADERS = json.loads(os.getenv("GLOBAL_HEADERS", "{}"))

redis_client: Optional[redis.Redis] = None


async def update_job_status(job_id: str, status: str, result=None, error=None):
    payload = {
        "status": status,
        "updated_at": time.time(),
        "result": result,
        "error": error
    }
    try:
        if redis_client:
            await redis_client.set(
                f"{REDIS_JOB_PREFIX}{job_id}",
                json.dumps(payload),
                ex=REDIS_EXPIRATION
            )
    except Exception as e:
        logger.error(f"Redis status update failed for {job_id}: {e}")


async def process_job(job: Dict[str, Any]):
    job_id = job['job_id']
    target_url = job['target_api_url']
    method = job.get('request_method', 'GET').upper()
    request_body = job.get('request_body')
    
    headers = GLOBAL_HEADERS.copy()
    if job.get('request_headers'):
        headers.update(job['request_headers'])

    logger.info(f"Processing {job_id} -> {method} {target_url}")

    async with httpx.AsyncClient() as client:
        try:
            if method == 'POST':
                response = await client.post(target_url, headers=headers, json=request_body, timeout=30.0)
            elif method == 'PUT':
                response = await client.put(target_url, headers=headers, json=request_body, timeout=30.0)
            elif method == 'DELETE':
                response = await client.delete(target_url, headers=headers, timeout=30.0)
            else:
                response = await client.get(target_url, headers=headers, timeout=30.0)

            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = {"raw_text": response.text}

            logger.info(f"Success {job_id}: {response.status_code}")

            webhook_payload = job.copy()
            webhook_payload.pop("request_headers", None)
            webhook_payload.pop("request_body", None)
            webhook_payload["api_status_code"] = response.status_code
            webhook_payload["api_response"] = response_data
            webhook_payload["processed_at"] = time.time()

            webhook_url = job.get('callback_webhook_url')
            webhook_status = None
            
            if webhook_url:
                try:
                    webhook_response = await client.post(webhook_url, json=webhook_payload, timeout=10.0)
                    webhook_status = webhook_response.status_code
                    logger.info(f"Webhook sent for {job_id}: {webhook_status}")
                except Exception as e:
                    webhook_status = "failed"
                    logger.error(f"Webhook failed for {job_id}: {e}")

            final_status = "completed" if webhook_status != "failed" else "completed_webhook_failed"
            await update_job_status(job_id, final_status, result=response_data)

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            await update_job_status(job_id, "failed", error=str(e))


async def queue_worker():
    rate_per_minute = 60 / SECONDS_BETWEEN_REQUESTS
    logger.info(f"Worker started. Rate limit: {rate_per_minute:.0f} requests/minute")

    while True:
        try:
            if redis_client:
                result = await redis_client.blpop(REDIS_QUEUE_KEY, timeout=0)

                if result:
                    _, raw_data = result
                    job = json.loads(raw_data)

                    start_time = time.monotonic()

                    try:
                        await asyncio.wait_for(process_job(job), timeout=25.0)
                    except asyncio.TimeoutError:
                        logger.error(f"Job {job.get('job_id')} timed out")
                        await update_job_status(job['job_id'], "timeout")

                    elapsed = time.monotonic() - start_time
                    sleep_time = max(0, SECONDS_BETWEEN_REQUESTS - elapsed)
                    await asyncio.sleep(sleep_time)
            else:
                await asyncio.sleep(1)

        except Exception as e:
            logger.critical(f"Worker error: {e}")
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    try:
        logger.info(f"Connecting to Redis: {REDIS_URL}")
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
        logger.info("Redis connected")
        asyncio.create_task(queue_worker())
    except Exception as e:
        logger.critical(f"Redis connection failed: {e}")
    
    yield
    
    if redis_client:
        await redis_client.close()
        logger.info("Redis disconnected")


app = FastAPI(lifespan=lifespan)


@app.post("/process")
async def queue_job(request: Request, response: Response):
    try:
        body = await request.json()
    except Exception:
        response.status_code = 400
        return {"error": "Invalid JSON"}

    if "target_api_url" not in body:
        if "target_api" in body:
            body["target_api_url"] = body.pop("target_api")
        else:
            response.status_code = 400
            return {"error": "Missing required field: target_api_url"}

    job_id = str(uuid.uuid4())
    body['job_id'] = job_id
    body['queued_at'] = time.time()

    queue_position = -1
    if redis_client:
        await update_job_status(job_id, "queued")
        queue_position = await redis_client.rpush(REDIS_QUEUE_KEY, json.dumps(body))

    logger.info(f"Queued {job_id} at position {queue_position}")

    response.status_code = 202
    return {
        "job_id": job_id,
        "status": "queued",
        "queue_position": queue_position
    }


@app.get("/status/{job_id}")
async def get_job_status(job_id: str):
    if redis_client:
        data = await redis_client.get(f"{REDIS_JOB_PREFIX}{job_id}")
        if data:
            return json.loads(data)
    return {"status": "not_found"}


@app.get("/health")
async def health():
    redis_ok = False
    queue_depth = 0
    
    if redis_client:
        try:
            await redis_client.ping()
            redis_ok = True
            queue_depth = await redis_client.llen(REDIS_QUEUE_KEY)
        except:
            pass

    return {
        "status": "ok" if redis_ok else "degraded",
        "redis_connected": redis_ok,
        "queue_depth": queue_depth,
        "rate_limit": f"{60/SECONDS_BETWEEN_REQUESTS:.0f}/min"
    }


@app.get("/")
async def root():
    return {
        "service": "Rate-Limited API Proxy",
        "endpoints": {
            "POST /process": "Queue a new API request",
            "GET /status/{job_id}": "Check job status",
            "GET /health": "Service health check"
        }
    }
