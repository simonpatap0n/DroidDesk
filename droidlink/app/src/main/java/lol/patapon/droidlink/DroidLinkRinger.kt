package lol.patapon.droidlink

import android.content.Context
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Build
import android.os.Handler
import android.os.Looper

object DroidLinkRinger {
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var ringtone: Ringtone? = null
    private val stopTask = Runnable { stopNow() }

    fun ring(context: Context) {
        main.post {
            stopNow()
            val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
                ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
                ?: return@post
            ringtone = RingtoneManager.getRingtone(context.applicationContext, uri)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) ringtone?.isLooping = true
            ringtone?.play()
            main.postDelayed(stopTask, 30_000L)
        }
    }

    fun stop() {
        main.post { stopNow() }
    }

    private fun stopNow() {
        main.removeCallbacks(stopTask)
        ringtone?.stop()
        ringtone = null
    }
}
