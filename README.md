# Rate-Limited API Proxy & LinkedIn Scraper Queue

A smart proxy service that queues API requests and processes them at a controlled pace (Rate Limiting). Designed to work seamlessly with **Clay.com** to avoid blocking your accounts (e.g., LinkedIn/RapidAPI) when processing large batches of data.

---

## 🚀 Key Features

1.  **Rate Limiting**: Queues requests in Redis and processes them one by one (e.g., every 3.5 seconds).
2.  **Synchronous Mode (New!)**: The API waits for the job to complete and returns the result directly in the response. Perfect for Clay's "HTTP API" action.
3.  **LinkedIn Scraper**: Built-in logic to scrape LinkedIn profiles, posts, and reactions using RapidAPI.
4.  **Long Timeout Support**: Supports waiting up to 1 hour for job completion, handling large queues.

---

## 🛠️ Configuration (Railway)

This app requires **Redis**. Set these environment variables in your hosting provider (e.g., Railway):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `REDIS_URL` | - | **Required**. Connection string for your Redis instance. |
| `RATE_LIMIT_DELAY` | `3.5` | Seconds to wait between processing jobs. `3.5` ≈ 17 requests/minute. |
| `GLOBAL_HEADERS` | `{}` | JSON string with default headers. |

**Important for LinkedIn Scraper:**
You must set `GLOBAL_HEADERS` to include your RapidAPI credentials:
```json
{"x-rapidapi-key": "YOUR_REAL_API_KEY", "x-rapidapi-host": "fresh-linkedin-scraper-api.p.rapidapi.com"}
```

---

## 📖 API Usage

### Endpoint: `POST /process`

This is the main endpoint you will use in Clay.

#### 1. LinkedIn Profile Scraper (Recommended)

Use this to get profile data, posts, and reactions.

**Request Body:**
```json
{
  "username": "williamhgates",  // LinkedIn public ID
  "api_key": "YOUR_RAPIDAPI_KEY", // Can also be set in GLOBAL_HEADERS env var
  "include_experiences": true,
  "sync": true                  // Default: true. Waits for result.
}
```

**Response (Success):**
Returns the full JSON object with profile data, experiences, posts, etc.

**Response (Timeout):**
If the queue is too long (> 1 hour), returns `504 Gateway Timeout`.

#### 2. Generic API Proxy

Use this to call *any* API with rate limiting.

**Request Body:**
```json
{
  "target_api_url": "https://api.example.com/data",
  "request_method": "GET",
  "sync": true
}
```

---

## 🧱 Clay.com Integration

1.  Add an **"HTTP API"** column (or "Enrich Data" -> "HTTP API").
2.  **Method**: `POST`
3.  **URL**: `https://YOUR-APP-DOMAIN.up.railway.app/process`
4.  **Headers**: `Content-Type: application/json`
5.  **Body**:
    ```json
    {
      "username": "{{LinkedIn URL}}", 
      "api_key": "YOUR_KEY",
      "sync": true
    }
    ```
    *(Note: You might need to use a formula to extract the username from a full LinkedIn URL)*

6.  **Run**: Clay will send the request, wait (potentially minutes), and put the final result in the cell.

---

## ⚡ Asynchronous Mode (Webhooks)

If you prefer to receive results via a webhook (instead of waiting):

1.  Set `"sync": false` in your request body.
2.  Provide `"callback_webhook_url": "https://hooks.clay.com/..."`.
3.  The API will immediately return a `job_id`.
4.  When the job is done, the server will POST the result to your webhook URL.

---

## 🏥 Health Check

**GET** `/health`
Returns the status of the Redis connection and current queue depth.
