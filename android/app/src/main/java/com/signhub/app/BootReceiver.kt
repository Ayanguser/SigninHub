package com.signhub.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * 开机后恢复定时。
 *
 * WorkManager 本身会跨重启保留任务，所以这里主要是「兜底」——
 * 部分国产 ROM 的省电策略会清掉 WorkManager 的数据库，重排一次更保险。
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent?) {
        val action = intent?.action ?: return
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != "android.intent.action.QUICKBOOT_POWERON"
        ) {
            return
        }
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val s = SettingsStore.flow(ctx).first()
                val c = LocalConfig.load(ctx)
                // 老的提醒通道（只弹通知）
                if (s.reminderEnabled) {
                    ReminderScheduler.schedule(ctx, true, s.reminderTime)
                }
                // 新的自动签到（真的执行签到）
                AutoCheckinScheduler.schedule(ctx, c.schedulerEnabled, c.schedulerTime)
            } finally {
                pending.finish()
            }
        }
    }
}
