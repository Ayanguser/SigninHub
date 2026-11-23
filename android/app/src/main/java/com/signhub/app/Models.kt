package com.signhub.app

import org.json.JSONObject

data class ProviderStatus(
    val key: String,
    val label: String,
    val enabled: Boolean,
    val mode: String,
    val dryRun: Boolean,
    val status: String,
    val message: String,
    val points: String,
    val time: String,
) {
    companion object {
        fun from(o: JSONObject) = ProviderStatus(
            key = o.optString("key"),
            label = o.optString("label"),
            enabled = o.optBoolean("enabled", true),
            mode = o.optString("mode", "http"),
            dryRun = o.optBoolean("dry_run", false),
            status = o.optString("status", "idle"),
            message = o.optString("message", ""),
            points = o.optString("points", ""),
            time = o.optString("time", ""),
        )
    }
}

data class Stats(val total: Int, val success: Int, val fail: Int)

data class StatusResponse(
    val day: String,
    val providers: List<ProviderStatus>,
    val schedulerEnabled: Boolean,
    val schedulerTimes: List<String>,
    val stats: Stats,
) {
    companion object {
        fun from(json: JSONObject): StatusResponse {
            val arr = json.optJSONArray("providers")
            val list = mutableListOf<ProviderStatus>()
            if (arr != null) for (i in 0 until arr.length()) list.add(ProviderStatus.from(arr.getJSONObject(i)))
            val sc = json.optJSONObject("scheduler")
            val times = mutableListOf<String>()
            sc?.optJSONArray("times")?.let { t -> for (i in 0 until t.length()) times.add(t.getString(i)) }
            val st = json.optJSONObject("stats")
            return StatusResponse(
                day = json.optString("day", ""),
                providers = list,
                schedulerEnabled = sc?.optBoolean("enabled", true) ?: true,
                schedulerTimes = times,
                stats = Stats(
                    total = st?.optInt("total", 0) ?: 0,
                    success = st?.optInt("success", 0) ?: 0,
                    fail = st?.optInt("fail", 0) ?: 0,
                ),
            )
        }
    }
}

data class LogItem(
    val time: String,
    val provider: String,
    val status: String,
    val points: String,
    val message: String,
) {
    companion object {
        fun from(o: JSONObject) = LogItem(
            time = o.optString("created_at", "").replace("T", " ").take(19),
            provider = o.optString("provider", ""),
            status = o.optString("status", ""),
            points = o.optString("points", ""),
            message = o.optString("message", ""),
        )
    }
}
