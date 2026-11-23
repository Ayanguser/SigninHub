package com.signhub.app

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings as AndroidSettings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.launch

private val Brand = Color(0xFF5B8CFF)
private val BgColor = Color(0xFF0F1115)
private val CardColor = Color(0xFF171A21)
private val Muted = Color(0xFF8A93A6)
private val Ok = Color(0xFF38D39F)
private val Warn = Color(0xFFF5C451)
private val Bad = Color(0xFFFF5C72)

class MainActivity : ComponentActivity() {

    private val notifPerm = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 33) {
            notifPerm.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Brand, background = BgColor)) {
                val vm: MainViewModel = viewModel()
                AppRoot(vm)
            }
        }
    }
}

@Composable
fun AppRoot(vm: MainViewModel) {
    val st by vm.state.collectAsStateWithLifecycle()
    var tab by remember { mutableIntStateOf(0) }
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(st.toast) {
        st.toast?.let {
            snackbar.showSnackbar(it)
            vm.clearToast()
        }
    }

    Scaffold(
        containerColor = BgColor,
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            NavigationBar(containerColor = CardColor) {
                NavigationBarItem(
                    selected = tab == 0, onClick = { tab = 0 },
                    icon = { Icon(Icons.Filled.Dashboard, null) }, label = { Text("控制台") })
                NavigationBarItem(
                    selected = tab == 1, onClick = { tab = 1 },
                    icon = { Icon(Icons.Filled.List, null) }, label = { Text("记录") })
                NavigationBarItem(
                    selected = tab == 2, onClick = { tab = 2 },
                    icon = { Icon(Icons.Filled.Settings, null) }, label = { Text("设置") })
            }
        },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            when (tab) {
                0 -> DashboardScreen(st, vm)
                1 -> LogsScreen(st)
                else -> SettingsScreen(vm)
            }
        }
    }
}

@Composable
fun DashboardScreen(st: UiState, vm: MainViewModel) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.weight(1f)) {
                Text("签到中心", fontSize = 22.sp, fontWeight = FontWeight.Bold, color = Color.White)
                Text(
                    if (st.localMode) "本地模式 · 不依赖电脑"
                    else if (st.day.isNotBlank()) "今日 ${st.day} · 定时 ${st.schedulerText}"
                    else "连接电脑端服务",
                    fontSize = 12.sp,
                    color = if (st.localMode) Ok else Muted
                )
            }
            IconButton(onClick = { if (st.localMode) vm.runLocalAll() else vm.refreshAll() }) {
                Icon(Icons.Filled.Refresh, null, tint = Brand)
            }
        }
        Spacer(Modifier.height(14.dp))

        if (st.localMode) {
            // ── 本地模式：手机自己签 ──
            Button(
                onClick = { vm.runLocalAll() }, enabled = !st.loading,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Brand)
            ) {
                if (st.loading) CircularProgressIndicator(
                    Modifier.size(18.dp), color = Color.White, strokeWidth = 2.dp)
                else {
                    Icon(Icons.Filled.PlayArrow, null); Spacer(Modifier.width(6.dp))
                    Text("一键签到（WB + Trae）")
                }
            }
            Spacer(Modifier.height(14.dp))

            val shown = st.localResults.ifEmpty {
                listOf(
                    CheckinResult("workbuddy", "idle", "未执行"),
                    CheckinResult("traesolo", "idle", "未执行"),
                    CheckinResult("qoder", "idle", "未执行"),
                    CheckinResult("minimax", "idle", "未执行"),
                )
            }
            LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                items(shown) { r ->
                    LocalProviderCard(r) { vm.runLocalOne(r.provider) }
                }
            }
        } else {
            // ── 远程模式：遥控电脑 ──
            Button(
                onClick = { vm.runAll() }, enabled = !st.loading,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Brand)
            ) {
                if (st.loading) CircularProgressIndicator(
                    Modifier.size(18.dp), color = Color.White, strokeWidth = 2.dp)
                else {
                    Icon(Icons.Filled.PlayArrow, null); Spacer(Modifier.width(6.dp))
                    Text("一键全部签到")
                }
            }
            Spacer(Modifier.height(8.dp))
            Text(
                "累计 ${st.stats.total} · 成功 ${st.stats.success} · 失败 ${st.stats.fail}",
                fontSize = 12.sp, color = Muted
            )
            Spacer(Modifier.height(12.dp))

            st.error?.let {
                Card(colors = CardDefaults.cardColors(containerColor = Color(0x33FF5C72))) {
                    Text(it, color = Color(0xFFFFC0C8), fontSize = 13.sp,
                        modifier = Modifier.padding(12.dp))
                }
                Spacer(Modifier.height(12.dp))
            }

            LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                items(st.providers) { p -> ProviderCard(p) { vm.runOne(p.key, p.label) } }
            }
        }
    }
}

private fun localLabel(key: String) = when (key) {
    "workbuddy" -> "WorkBuddy"
    "traesolo" -> "Trae Solo"
    "qoder" -> "Qoder CN"
    "minimax" -> "MiniMax Code"
    else -> key
}

@Composable
fun LocalProviderCard(r: CheckinResult, onRun: () -> Unit) {
    val color = when (r.status) {
        "success" -> Ok
        "already" -> Warn
        "fail" -> Bad
        else -> Muted
    }
    val statusText = when (r.status) {
        "success" -> "已签到"
        "already" -> "今日已签到"
        "fail" -> "失败"
        else -> "未执行"
    }
    Card(colors = CardDefaults.cardColors(containerColor = CardColor),
        shape = RoundedCornerShape(16.dp)) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(localLabel(r.provider), fontSize = 17.sp, fontWeight = FontWeight.Bold,
                    color = Color.White, modifier = Modifier.weight(1f))
                if (r.points.isNotBlank())
                    Text(r.points, color = color, fontSize = 15.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(9.dp).background(color, RoundedCornerShape(50)))
                Spacer(Modifier.width(8.dp))
                Text(statusText, color = color, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            }
            Spacer(Modifier.height(6.dp))
            Text(r.message.ifBlank { "—" }, fontSize = 12.sp, color = Muted, maxLines = 3)
            Spacer(Modifier.height(10.dp))
            OutlinedButton(onClick = onRun, shape = RoundedCornerShape(10.dp),
                modifier = Modifier.fillMaxWidth()) { Text("签到") }
        }
    }
}

@Composable
fun ProviderCard(p: ProviderStatus, onRun: () -> Unit) {
    val color = when (p.status) {
        "success" -> Ok
        "already" -> Warn
        "fail" -> Bad
        else -> Muted
    }
    val statusText = when (p.status) {
        "success" -> "已签到"
        "already" -> "今日已签到"
        "fail" -> "失败"
        else -> "未执行"
    }
    Card(colors = CardDefaults.cardColors(containerColor = CardColor), shape = RoundedCornerShape(16.dp)) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(p.label, fontSize = 17.sp, fontWeight = FontWeight.Bold, color = Color.White,
                    modifier = Modifier.weight(1f))
                AssistChip(
                    onClick = {},
                    label = {
                        Text((if (p.mode == "browser") "浏览器" else "接口") +
                            if (p.dryRun) "·演示" else "", fontSize = 11.sp, color = Muted)
                    },
                )
            }
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(9.dp).background(color, RoundedCornerShape(50)))
                Spacer(Modifier.width(8.dp))
                Text(statusText, color = color, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.weight(1f))
                if (p.points.isNotBlank())
                    Text(p.points, color = color, fontSize = 20.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(6.dp))
            Text(p.message.ifBlank { "—" }, fontSize = 12.sp, color = Muted, maxLines = 2)
            Spacer(Modifier.height(10.dp))
            OutlinedButton(
                onClick = onRun, shape = RoundedCornerShape(10.dp),
                modifier = Modifier.fillMaxWidth(),
            ) { Text(if (p.status == "success") "再签到一次" else "签到") }
        }
    }
}

@Composable
fun LogsScreen(st: UiState) {
    LazyColumn(Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            Text("最近记录", fontSize = 20.sp, fontWeight = FontWeight.Bold, color = Color.White)
            Spacer(Modifier.height(8.dp))
        }
        if (st.localMode) {
            if (st.localResults.isEmpty()) {
                item { Text("本次运行还没有记录，去控制台点签到", color = Muted) }
            }
            items(st.localResults) { r ->
                val color = when (r.status) {
                    "success" -> Ok; "already" -> Warn; "fail" -> Bad; else -> Muted
                }
                Card(colors = CardDefaults.cardColors(containerColor = CardColor),
                    shape = RoundedCornerShape(12.dp)) {
                    Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("${localLabel(r.provider)}  ${r.points}", color = Color.White,
                                fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                            Text(r.message, color = Muted, fontSize = 11.sp, maxLines = 2)
                        }
                        Text(r.status, color = color, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                    }
                }
            }
            return@LazyColumn
        }
        if (st.logs.isEmpty()) {
            item { Text("暂无记录", color = Muted) }
        }
        items(st.logs) { l ->
            val color = when (l.status) {
                "success" -> Ok; "already" -> Warn; "fail" -> Bad; else -> Muted
            }
            Card(colors = CardDefaults.cardColors(containerColor = CardColor),
                shape = RoundedCornerShape(12.dp)) {
                Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("${l.provider}  ${l.points}", color = Color.White,
                            fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                        Text(l.time, color = Muted, fontSize = 11.sp)
                        Text(l.message, color = Muted, fontSize = 11.sp, maxLines = 1)
                    }
                    Text(l.status, color = color, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}

@Composable
fun SettingsScreen(vm: MainViewModel) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var loaded by remember { mutableStateOf(false) }

    var localMode by remember { mutableStateOf(false) }
    var url by remember { mutableStateOf("http://192.168.1.100:8787") }
    var token by remember { mutableStateOf("") }
    var reminderOn by remember { mutableStateOf(false) }
    var reminderTime by remember { mutableStateOf("08:30") }
    var wbToken by remember { mutableStateOf("") }
    var wbUid by remember { mutableStateOf("") }
    var wbEnt by remember { mutableStateOf("") }
    var wbDomain by remember { mutableStateOf("") }
    var wbGlobal by remember { mutableStateOf(false) }
    var traeToken by remember { mutableStateOf("") }
    var traeDevice by remember { mutableStateOf("") }
    var traeGlobal by remember { mutableStateOf(false) }
    var traeSession by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        SettingsStore.flow(ctx).collect { s ->
            if (!loaded) {
                localMode = s.localMode
                url = s.serverUrl; token = s.token
                reminderOn = s.reminderEnabled; reminderTime = s.reminderTime
                wbToken = s.wbToken; wbUid = s.wbUid; wbEnt = s.wbEnterpriseId
                wbDomain = s.wbDomain; wbGlobal = s.wbGlobal
                traeToken = s.traeToken; traeDevice = s.traeDeviceId; traeGlobal = s.traeGlobal
                traeSession = s.traeSession
                loaded = true
            }
        }
    }

    fun persist(extra: (AppSettings) -> AppSettings = { it }) {
        scope.launch {
            val base = vm.currentSettings().copy(
                localMode = localMode,
                serverUrl = url.trim(), token = token.trim(),
                reminderEnabled = reminderOn, reminderTime = reminderTime,
                wbToken = wbToken.trim(), wbUid = wbUid.trim(),
                wbEnterpriseId = wbEnt.trim(), wbDomain = wbDomain.trim(),
                wbGlobal = wbGlobal,
                traeToken = traeToken.trim(), traeDeviceId = traeDevice.trim(),
                traeGlobal = traeGlobal, traeSession = traeSession.trim(),
            )
            SettingsStore.save(ctx, extra(base))
            if (!localMode) vm.refreshAll()
        }
    }

    Column(
        Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState())
    ) {
        Text("设置", fontSize = 20.sp, fontWeight = FontWeight.Bold, color = Color.White)
        Spacer(Modifier.height(16.dp))

        // ── 运行模式 ──
        Card(colors = CardDefaults.cardColors(containerColor = CardColor),
            shape = RoundedCornerShape(14.dp)) {
            Column(Modifier.padding(14.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("本地模式（不依赖电脑）", color = Color.White,
                            fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                        Text(
                            if (localMode) "手机直接调官方接口签到"
                            else "遥控电脑上的服务签到",
                            fontSize = 11.sp, color = if (localMode) Ok else Muted
                        )
                    }
                    Switch(checked = localMode, onCheckedChange = {
                        localMode = it
                        persist()
                    })
                }
            }
        }
        Spacer(Modifier.height(16.dp))

        if (!localMode) {
            Text("电脑端连接", fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = Color.White)
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = url, onValueChange = { url = it },
                label = { Text("电脑端服务地址") },
                placeholder = { Text("http://192.168.1.100:8787") },
                modifier = Modifier.fillMaxWidth(), singleLine = true,
            )
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(
                value = token, onValueChange = { token = it },
                label = { Text("API Token（后端未设置可留空）") },
                modifier = Modifier.fillMaxWidth(), singleLine = true,
            )
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = { persist { it }.also { vm.refreshAll() } },
                modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Brand),
            ) { Text("保存并连接") }
        } else {
            // ── WorkBuddy 凭据 ──
            Text("WorkBuddy 凭据", fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = Color.White)
            Spacer(Modifier.height(6.dp))
            Text("accessToken 来自电脑端 ~/.dsh/workbuddy-vault/cn/*.json 里的 accessToken 字段",
                fontSize = 11.sp, color = Muted)
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = wbToken, onValueChange = { wbToken = it },
                label = { Text("accessToken") },
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = wbUid, onValueChange = { wbUid = it },
                label = { Text("uid（X-User-Id）") },
                modifier = Modifier.fillMaxWidth(), singleLine = true,
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = wbEnt, onValueChange = { wbEnt = it },
                label = { Text("企业ID（X-Enterprise-Id，可空）") },
                modifier = Modifier.fillMaxWidth(), singleLine = true,
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = wbDomain, onValueChange = { wbDomain = it },
                label = { Text("domain（可空）") },
                modifier = Modifier.fillMaxWidth(), singleLine = true,
            )
            Spacer(Modifier.height(6.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("国际版 (workbuddy.ai)", color = Color.White, modifier = Modifier.weight(1f))
                Switch(checked = wbGlobal, onCheckedChange = { wbGlobal = it })
            }

            Spacer(Modifier.height(18.dp))
            HorizontalDivider(color = Color(0xFF2A2F3A))
            Spacer(Modifier.height(14.dp))

            // ── Trae 凭据 ──
            Text("Trae Solo 凭据", fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = Color.White)
            Spacer(Modifier.height(6.dp))
            Text("Cloud-IDE-Token 约 8 小时就过期。填下面的 X-Cloudide-Session（约 14 天）之后，" +
                    "token 到期会自动换新，定时任务不会因为过期而失败。",
                fontSize = 11.sp, color = Muted)
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = traeSession, onValueChange = { traeSession = it },
                label = { Text("X-Cloudide-Session（推荐，自动续期）") },
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = traeToken, onValueChange = { traeToken = it },
                label = { Text("Cloud-IDE-Token（可选，8 小时有效）") },
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = traeDevice, onValueChange = { traeDevice = it },
                label = { Text("x-device-id（16 位数字）") },
                modifier = Modifier.fillMaxWidth(), singleLine = true,
            )
            Spacer(Modifier.height(6.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("国际版 (trae.ai)", color = Color.White, modifier = Modifier.weight(1f))
                Switch(checked = traeGlobal, onCheckedChange = { traeGlobal = it })
            }

            Spacer(Modifier.height(18.dp))
            Button(
                onClick = { persist() },
                modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Brand),
            ) { Text("保存凭据") }
        }

        Spacer(Modifier.height(20.dp))
        HorizontalDivider(color = Color(0xFF2A2F3A))
        Spacer(Modifier.height(16.dp))

        Text("每日提醒", fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = Color.White)
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("开启提醒", color = Color.White, modifier = Modifier.weight(1f))
            Switch(checked = reminderOn, onCheckedChange = { reminderOn = it })
        }
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = reminderTime, onValueChange = { reminderTime = it },
            label = { Text("提醒时间（HH:mm）") },
            modifier = Modifier.fillMaxWidth(), singleLine = true,
        )
        Spacer(Modifier.height(10.dp))
        OutlinedButton(
            onClick = {
                persist {
                    ReminderScheduler.schedule(ctx, reminderOn, reminderTime)
                    it
                }
            },
            modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp),
        ) { Text("保存提醒") }

        Spacer(Modifier.height(20.dp))
        HorizontalDivider(color = Color(0xFF2A2F3A))
        Spacer(Modifier.height(16.dp))

        Text("手机侧兜底：无障碍自动点击", fontSize = 16.sp,
            fontWeight = FontWeight.SemiBold, color = Color.White)
        Spacer(Modifier.height(6.dp))
        Text(
            "开启后，本 App 可在官方 App 页面内自动点击「签到」按钮（无需抓包）。",
            fontSize = 12.sp, color = Muted,
        )
        Spacer(Modifier.height(10.dp))
        Button(
            onClick = {
                ctx.startActivity(
                    Intent(AndroidSettings.ACTION_ACCESSIBILITY_SETTINGS)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            },
            modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp),
            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF2A2F3A)),
        ) {
            Icon(Icons.Filled.TouchApp, null); Spacer(Modifier.width(8.dp))
            Text("打开无障碍设置")
        }
        Spacer(Modifier.height(24.dp))
    }
}
