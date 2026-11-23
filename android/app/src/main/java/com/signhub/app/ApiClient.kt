package com.signhub.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

class ApiClient(private val baseUrl: String, private val token: String) {
    // N3（BUGS-v2.1）：显式 callTimeout，见 WbMulti.kt 同处注释。
    private val client = OkHttpClient.Builder()
        .connectTimeout(8, java.util.concurrent.TimeUnit.SECONDS)
        .readTimeout(25, java.util.concurrent.TimeUnit.SECONDS)
        .callTimeout(30, java.util.concurrent.TimeUnit.SECONDS)
        .build()

    private fun url(path: String) = baseUrl.trimEnd('/') + path

    private fun req(path: String): Request.Builder {
        val b = Request.Builder().url(url(path))
        if (token.isNotBlank()) b.addHeader("X-API-Token", token)
        return b
    }

    suspend fun status(): StatusResponse = withContext(Dispatchers.IO) {
        client.newCall(req("/api/status").get().build()).execute().use { r ->
            val body = r.body?.string() ?: "{}"
            if (!r.isSuccessful) throw RuntimeException("HTTP ${r.code}: $body")
            StatusResponse.from(JSONObject(body))
        }
    }

    suspend fun runAll(): JSONObject = withContext(Dispatchers.IO) {
        val empty = "{}".toRequestBody("application/json".toMediaType())
        client.newCall(req("/api/checkin/all").post(empty).build()).execute().use { r ->
            val body = r.body?.string() ?: "{}"
            if (!r.isSuccessful) throw RuntimeException("HTTP ${r.code}: $body")
            JSONObject(body)
        }
    }

    suspend fun runOne(key: String): JSONObject = withContext(Dispatchers.IO) {
        val empty = "{}".toRequestBody("application/json".toMediaType())
        client.newCall(req("/api/checkin/$key").post(empty).build()).execute().use { r ->
            val body = r.body?.string() ?: "{}"
            if (!r.isSuccessful) throw RuntimeException("HTTP ${r.code}: $body")
            JSONObject(body)
        }
    }

    suspend fun logs(limit: Int = 40): List<LogItem> = withContext(Dispatchers.IO) {
        client.newCall(req("/api/logs?limit=$limit").get().build()).execute().use { r ->
            val body = r.body?.string() ?: "{}"
            if (!r.isSuccessful) throw RuntimeException("HTTP ${r.code}: $body")
            val arr = JSONObject(body).optJSONArray("logs")
            val out = mutableListOf<LogItem>()
            if (arr != null) for (i in 0 until arr.length()) out.add(LogItem.from(arr.getJSONObject(i)))
            out
        }
    }

    suspend fun ping(): Boolean = withContext(Dispatchers.IO) {
        try {
            client.newCall(req("/api/ping").get().build()).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            false
        }
    }
}
