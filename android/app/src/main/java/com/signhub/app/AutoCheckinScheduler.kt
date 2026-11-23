package com.signhub.app

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import java.time.Duration
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.util.concurrent.TimeUnit
import kotlin.random.Random

/**
 * 自动签到的定时调度（WorkManager）。
 *
 * 为什么用 WorkManager 而不是 AlarmManager：
 *   - AlarmManager 的 BroadcastReceiver 只有约 10 秒，两个网络签到容易超时
 *   - Android 12+ 精确闹钟要额外权限（SCHEDULE_EXACT_ALARM），用户可能不给
 *   - WorkManager 会等网络可用，受 Doze 影响但一定会在维护窗口执行
 *
 * 为什么不用 PeriodicWorkRequest：
 *   Periodic 的周期固定 24 小时，一旦某次被 Doze 延后 2 小时，
 *   **之后每天都会固定在那个延后的时刻**（漂移）。
 *   这里改用「单次任务 + 每次跑完自己排下一次」，保证每天都贴着目标时间。
 *
 * 随机浮动（jitter）：
 *   固定到点打卡的行为特征太明显（每天同一分钟、同一顺序）。
 *   每次排期时在目标时间后叠加 0~jitterMin 分钟的随机偏移，
 *   让实际执行时刻散布在一个窗口内。jitterMin=0 时行为与之前完全一致。
 */
object AutoCheckinScheduler {

    const val WORK_NAME = "signhub_auto_checkin_next"
    /** 「立即签到」用的独立 unique 名，与日常排期互不干扰。 */
    const val WORK_NAME_NOW = "signhub_auto_checkin_now"
    const val DEFAULT_TIME = "08:30"

    /** 调度（或更新）下一次自动签到。enabled=false 则取消。 */
    fun schedule(ctx: Context, enabled: Boolean, hhmm: String = DEFAULT_TIME, jitterMin: Int = -1) {
        val wm = WorkManager.getInstance(ctx)
        if (!enabled) {
            wm.cancelUniqueWork(WORK_NAME)
            return
        }
        val j = if (jitterMin >= 0) jitterMin else LocalConfig.load(ctx).schedulerJitter
        val (h, m) = parse(hhmm)
        enqueueAt(ctx, h, m, j)
    }

    /**
     * 确保有排期，但**不覆盖已经排好的那次**。
     *
     * 为什么需要这个：以前 App 每次 onCreate 都调 schedule()，而 schedule 内部是
     * REPLACE 策略。于是只要上午打开一次 App，那条「今天 08:30 还没执行」的任务
     * 就会被替换成「明天 08:30」——今天这一次直接消失。表现就是「开了 App 反而
     * 不签到了」。
     *
     * 这里改用 KEEP：已有任务就原样保留，只有完全没有任务时才新建。
     */
    fun ensureScheduled(ctx: Context, hhmm: String = DEFAULT_TIME, jitterMin: Int = -1) {
        val c = LocalConfig.load(ctx)
        if (!c.schedulerEnabled) return
        val j = if (jitterMin >= 0) jitterMin else c.schedulerJitter
        val (h, m) = parse(if (hhmm.isBlank()) c.schedulerTime else hhmm)
        val base = delayUntil(h, m)
        val extraMs = if (j > 0) Random.nextLong(0, j.toLong() * 60_000L + 1) else 0L
        val req = OneTimeWorkRequestBuilder<AutoCheckinWorker>()
            .setInitialDelay(base.toMillis() + extraMs, TimeUnit.MILLISECONDS)
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .addTag(WORK_NAME)
            .build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.KEEP, req)
    }

    /** 立刻跑一次（调试 / 补签）。跑完会自动排下一次。 */
    fun runNow(ctx: Context) {
        val req = OneTimeWorkRequestBuilder<AutoCheckinWorker>()
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .addTag(WORK_NAME_NOW)
            .build()
        // 必须用 unique：以前是裸 enqueue()，用户在设置页连点两下「立即签到」
        // 就会有两个 Worker 同时跑，各自打一遍上游（第二次全变「已签到」），
        // 还会同时写库、同时发通知。KEEP 表示「已经排了就沿用」，不会打断正在跑的。
        WorkManager.getInstance(ctx).enqueueUniqueWork(
            WORK_NAME_NOW, ExistingWorkPolicy.KEEP, req
        )
    }

    /** 内部：排到下一个 h:m（叠加 0~jitterMin 分钟随机偏移）。 */
    private fun enqueueAt(ctx: Context, h: Int, m: Int, jitterMin: Int = 0) {
        val base = delayUntil(h, m)
        val extraMs = if (jitterMin > 0) Random.nextLong(0, jitterMin.toLong() * 60_000L + 1) else 0L
        val req = OneTimeWorkRequestBuilder<AutoCheckinWorker>()
            .setInitialDelay(base.toMillis() + extraMs, TimeUnit.MILLISECONDS)
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .addTag(WORK_NAME)
            .build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(
            WORK_NAME, ExistingWorkPolicy.REPLACE, req
        )
    }

    /** Worker 跑完后调用：排下一天。 */
    fun scheduleNext(ctx: Context) {
        val c = LocalConfig.load(ctx)
        if (!c.schedulerEnabled) return
        val (h, m) = parse(c.schedulerTime)
        enqueueAt(ctx, h, m, c.schedulerJitter)
    }

    /* ---------- 运行记录（诊断用） ----------
     * 用户没法知道后台任务到底跑没跑：没跑是「系统没调度」，跑了失败是「凭据/风控」，
     * 两者处理方式完全不同。这里把每次运行的时间、标题、正文存下来，
     * 设置页可以直接看到「上次自动签到：MM-dd HH:mm · 结果」。 */
    private const val RUN_PREF = "signhub_auto_run"
    private const val K_AT = "at"
    private const val K_TITLE = "title"
    private const val K_BODY = "body"

    fun recordRun(ctx: Context, title: String, body: String) {
        try {
            val sp = ctx.getSharedPreferences(RUN_PREF, Context.MODE_PRIVATE)
            sp.edit()
                .putLong(K_AT, System.currentTimeMillis())
                .putString(K_TITLE, title)
                .putString(K_BODY, body)
                .apply()
        } catch (_: Exception) {
        }
    }

    /** 返回 {at, title, body}；没跑过返回 null。 */
    fun lastRun(ctx: Context): Map<String, Any>? {
        return try {
            val sp = ctx.getSharedPreferences(RUN_PREF, Context.MODE_PRIVATE)
            val at = sp.getLong(K_AT, 0L)
            if (at <= 0L) null
            else mapOf(
                "at" to at,
                "title" to (sp.getString(K_TITLE, "") ?: ""),
                "body" to (sp.getString(K_BODY, "") ?: ""),
            )
        } catch (_: Exception) {
            null
        }
    }

    fun parse(hhmm: String): Pair<Int, Int> {
        val parts = hhmm.split(":")
        val h = parts.getOrNull(0)?.toIntOrNull()?.coerceIn(0, 23) ?: 8
        val m = parts.getOrNull(1)?.toIntOrNull()?.coerceIn(0, 59) ?: 30
        return h to m
    }

    /**
     * 距离下一个 h:m 还有多久（若今天已过则算明天）。
     *
     * 必须按 Asia/Shanghai 算，不能用 LocalDateTime.now()：
     * 全 App 的「今天」都走 Asia/Shanghai（LocalApi.today()、HistoryStore.now()、
     * MultiAccountsActivity.today()），而这里以前用设备本地时区 ——
     * 设备时区不是 UTC+8 时，排期时刻会与「今天」的判定错位，
     * 表现为「到点了没签，或者提前/延后好几个小时才签」。
     */
    fun delayUntil(h: Int, m: Int): Duration {
        val zone = ZoneId.of("Asia/Shanghai")
        val now = LocalDateTime.now(zone)
        var target = LocalDateTime.of(now.toLocalDate(), LocalTime.of(h, m))
        if (!target.isAfter(now)) target = target.plusDays(1)
        return Duration.between(now, target)
    }
}
