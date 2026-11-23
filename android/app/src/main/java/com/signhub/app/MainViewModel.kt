package com.signhub.app

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class UiState(
    val loading: Boolean = false,
    val day: String = "",
    val providers: List<ProviderStatus> = emptyList(),
    val logs: List<LogItem> = emptyList(),
    val schedulerText: String = "",
    val stats: Stats = Stats(0, 0, 0),
    val error: String? = null,
    val toast: String? = null,
    val connected: Boolean = false,
    val localMode: Boolean = false,
    val localResults: List<CheckinResult> = emptyList(),
)

class MainViewModel(app: Application) : AndroidViewModel(app) {

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    private var settings = AppSettings()

    init {
        viewModelScope.launch {
            SettingsStore.flow(getApplication()).collect {
                settings = it
                _state.value = _state.value.copy(localMode = it.localMode)
                if (it.localMode) {
                    // 本地模式不需要连电脑，直接可用
                    _state.value = _state.value.copy(connected = true, error = null, loading = false)
                } else {
                    refreshAll()
                }
            }
        }
    }

    fun currentSettings(): AppSettings = settings

    /** 本地签到：WB / Trae / Qoder / MiniMax 都走纯 HTTP（不需要电脑）。 */
    fun runLocalAll() {
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            val out = mutableListOf<CheckinResult>()

            out.add(
                LocalCheckin.workbuddy(
                    settings.wbToken, settings.wbUid,
                    settings.wbEnterpriseId, settings.wbDomain, settings.wbGlobal
                )
            )
            out.add(
                LocalCheckin.trae(
                    settings.traeToken, settings.traeDeviceId, settings.traeGlobal, settings.traeSession
                ) { fresh ->
                    // 换到新 token 落盘（下一轮定时任务才不会又拿旧的 8 小时 JWT）
                    settings = settings.copy(traeToken = fresh)
                    val ctx = getApplication<Application>()
                    viewModelScope.launch {
                        runCatching { SettingsStore.save(ctx, settings) }
                    }
                }
            )

            // Qoder CN / MiniMax Code：纯 HTTP，手机直连即可签
            if (settings.qoderToken.isNotBlank() && settings.qoderMachineId.isNotBlank()) {
                out.add(LocalCheckin.qoder(
                    settings.qoderRegion.ifBlank { "cn" }, settings.qoderToken,
                    settings.qoderUid, settings.qoderMachineId, ""))
            }
            if (settings.mmToken.isNotBlank() && settings.mmUserId.isNotBlank()) {
                out.add(LocalCheckin.minimax(
                    settings.mmToken, settings.mmUserId, settings.mmDeviceId, ""))
            }

            val ok = out.count { it.status == "success" || it.status == "already" }
            _state.value = _state.value.copy(
                loading = false,
                localResults = out,
                toast = "本地签到完成：$ok/${out.size}",
            )
        }
    }

    fun runLocalOne(key: String) {
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true)
            val r = when (key) {
                "workbuddy" -> LocalCheckin.workbuddy(
                    settings.wbToken, settings.wbUid,
                    settings.wbEnterpriseId, settings.wbDomain, settings.wbGlobal
                )
                "traesolo" -> LocalCheckin.trae(
                    settings.traeToken, settings.traeDeviceId, settings.traeGlobal, settings.traeSession
                ) { fresh ->
                    settings = settings.copy(traeToken = fresh)
                    val ctx = getApplication<Application>()
                    viewModelScope.launch {
                        runCatching { SettingsStore.save(ctx, settings) }
                    }
                }
                "qoder" -> LocalCheckin.qoder(
                    settings.qoderRegion.ifBlank { "cn" }, settings.qoderToken,
                    settings.qoderUid, settings.qoderMachineId, "")
                "minimax" -> LocalCheckin.minimax(
                    settings.mmToken, settings.mmUserId, settings.mmDeviceId, "")
                else -> CheckinResult(key, "fail", "不支持的本地服务")            }
            val merged = _state.value.localResults.filterNot { it.provider == key } + r
            _state.value = _state.value.copy(loading = false, localResults = merged, toast = r.message)
        }
    }


    private fun client() = ApiClient(settings.serverUrl, settings.token)

    fun clearToast() {
        _state.value = _state.value.copy(toast = null)
    }

    fun refreshAll() {
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            try {
                val c = client()
                val st = c.status()
                val logs = try { c.logs(40) } catch (e: Exception) { emptyList() }
                _state.value = _state.value.copy(
                    loading = false,
                    connected = true,
                    day = st.day,
                    providers = st.providers,
                    logs = logs,
                    stats = st.stats,
                    schedulerText = if (st.schedulerEnabled) st.schedulerTimes.joinToString(" / ") else "已关闭",
                    error = null,
                )
            } catch (e: Exception) {
                _state.value = _state.value.copy(
                    loading = false,
                    connected = false,
                    error = "连接失败：${e.message}（检查地址/端口/Token，手机与电脑需同一网络）",
                )
            }
        }
    }

    fun runAll() {
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true)
            try {
                val r = client().runAll()
                val n = r.optJSONObject("results")?.length() ?: 0
                _state.value = _state.value.copy(loading = false, toast = "已执行 $n 个服务")
            } catch (e: Exception) {
                _state.value = _state.value.copy(loading = false, toast = "签到失败：${e.message}")
            }
            refreshAll()
        }
    }

    fun runOne(key: String, label: String) {
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true)
            try {
                val r = client().runOne(key)
                val status = r.optString("status", "")
                val points = r.optString("points", "")
                _state.value = _state.value.copy(
                    loading = false,
                    toast = "$label：$status ${if (points.isNotBlank()) points else ""}",
                )
            } catch (e: Exception) {
                _state.value = _state.value.copy(loading = false, toast = "$label 失败：${e.message}")
            }
            refreshAll()
        }
    }
}
