package com.signhub.app

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * 手机侧兜底执行器：在官方 App 页面内查找「签到 / 打卡 / 每日签到」等按钮并点击。
 * 需要用户在系统「无障碍」设置里手动开启本服务。仅点击可见的文字按钮，不做其它操作。
 */
class AutoClickService : AccessibilityService() {

    companion object {
        @Volatile
        var instance: AutoClickService? = null
        val KEYWORDS = listOf("签到", "打卡", "每日签到", "立即签到", "签到领积分", "领取")
        // 只在这些包名内执行，避免误触其它应用
        val TARGET_PACKAGES = setOf(
            "com.workbuddy.app",       // WorkBuddy（占位，按实际包名调整）
            "com.trae.solo",           // Trae Solo（占位，按实际包名调整）
        )
    }

    override fun onServiceConnected() {
        instance = this
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        val pkg = event?.packageName?.toString() ?: return
        // 允许对目标包或任意包（若用户希望通用）执行：此处严格限定目标包
        if (pkg !in TARGET_PACKAGES) return
        val root = rootInActiveWindow ?: return
        tryClickSignIn(root)
    }

    /** 供前台手动触发：在当前窗口尝试点击签到。 */
    fun tryClickNow(): Boolean {
        val root = rootInActiveWindow ?: return false
        return tryClickSignIn(root)
    }

    private fun tryClickSignIn(root: AccessibilityNodeInfo): Boolean {
        for (kw in KEYWORDS) {
            val nodes = root.findAccessibilityNodeInfosByText(kw) ?: continue
            for (node in nodes) {
                if (isClickableOrParent(node)) {
                    return performClick(node)
                }
            }
        }
        return false
    }

    private fun isClickableOrParent(node: AccessibilityNodeInfo?): Boolean {
        var n = node
        var depth = 0
        while (n != null && depth < 4) {
            if (n.isClickable) return true
            n = n.parent
            depth++
        }
        return false
    }

    private fun performClick(node: AccessibilityNodeInfo): Boolean {
        var n: AccessibilityNodeInfo? = node
        var depth = 0
        while (n != null && depth < 4) {
            if (n.isClickable) {
                return n.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            }
            n = n.parent
            depth++
        }
        return false
    }

    override fun onInterrupt() {}
}
