package lol.patapon.droidlink

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.Process
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

class DroidLinkAudioService : Service() {
    companion object {
        const val ACTION_START = "lol.patapon.droidlink.audio.START"
        const val ACTION_STOP = "lol.patapon.droidlink.audio.STOP"
        private const val CHANNEL_ID = "droidlink_microphone"
        private const val NOTIFICATION_ID = 7320
        private const val PREF_AUDIO_RUNNING = "audio_share_running"
        private const val SAMPLE_RATE = 16_000
        private const val CHUNK_BYTES = SAMPLE_RATE * 2 / 5 // 200 ms, mono PCM16

        fun start(context: Context) {
            val intent = Intent(context, DroidLinkAudioService::class.java).setAction(ACTION_START)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent)
            else context.startService(intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, DroidLinkAudioService::class.java))
            context.getSharedPreferences(DroidLinkTransport.PREFS, Context.MODE_PRIVATE)
                .edit().putBoolean(PREF_AUDIO_RUNNING, false).apply()
        }

        fun isRunning(context: Context): Boolean = context
            .getSharedPreferences(DroidLinkTransport.PREFS, Context.MODE_PRIVATE)
            .getBoolean(PREF_AUDIO_RUNNING, false)
    }

    private val uploadQueue = ArrayBlockingQueue<ByteArray>(5)
    @Volatile private var closing = false
    private var token = ""
    private var recorder: AudioRecord? = null
    private var captureThread: Thread? = null
    private var uploadThread: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopCapture()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf(startId)
            return START_NOT_STICKY
        }
        token = getSharedPreferences(DroidLinkTransport.PREFS, MODE_PRIVATE)
            .getString(DroidLinkTransport.SESSION_TOKEN_KEY, null).orEmpty()
        if (token.isBlank() || checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            stopSelf(startId)
            return START_NOT_STICKY
        }
        createNotificationChannel()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            startForeground(
                NOTIFICATION_ID,
                microphoneNotification(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
            )
        } else {
            startForeground(NOTIFICATION_ID, microphoneNotification())
        }
        getSharedPreferences(DroidLinkTransport.PREFS, MODE_PRIVATE)
            .edit().putBoolean(PREF_AUDIO_RUNNING, true).apply()
        if (captureThread?.isAlive != true) {
            closing = false
            uploadQueue.clear()
            captureThread = Thread(::captureLoop, "DroidLinkMicrophone").apply { start() }
            uploadThread = Thread(::uploadLoop, "DroidLinkAudioUpload").apply { start() }
        }
        return START_NOT_STICKY
    }

    private fun createNotificationChannel() {
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Partage du micro", NotificationManager.IMPORTANCE_LOW)
                .apply { description = "Indique quand DroidLink transmet le microphone à DroidDesk." },
        )
    }

    private fun microphoneNotification(): Notification {
        val stopIntent = PendingIntent.getService(
            this,
            NOTIFICATION_ID,
            Intent(this, DroidLinkAudioService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(applicationInfo.icon)
            .setContentTitle("Microphone partagé avec DroidDesk")
            .setContentText("La voix du téléphone est transmise. Touche Arrêter pour couper le micro.")
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .addAction(0, "Arrêter", stopIntent)
            .build()
    }

    private fun captureLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_AUDIO)
        val minimum = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minimum <= 0) {
            failCapture()
            return
        }
        val audio = runCatching {
            AudioRecord.Builder()
                .setAudioSource(MediaRecorder.AudioSource.MIC)
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setSampleRate(SAMPLE_RATE)
                        .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .build(),
                )
                .setBufferSizeInBytes(maxOf(minimum, CHUNK_BYTES * 3))
                .build()
        }.getOrNull()
        if (audio == null || audio.state != AudioRecord.STATE_INITIALIZED) {
            audio?.release()
            failCapture()
            return
        }
        recorder = audio
        try {
            audio.startRecording()
            val chunk = ByteArray(CHUNK_BYTES)
            var filled = 0
            while (!closing) {
                val count = audio.read(chunk, filled, CHUNK_BYTES - filled, AudioRecord.READ_BLOCKING)
                if (count < 0) throw IllegalStateException("Android n’a pas pu lire le micro.")
                filled += count
                if (filled >= CHUNK_BYTES) {
                    val pcm = chunk.copyOf()
                    if (!uploadQueue.offer(pcm)) {
                        uploadQueue.poll()
                        uploadQueue.offer(pcm)
                    }
                    filled = 0
                }
            }
        } catch (_: Exception) {
            if (!closing) failCapture()
        } finally {
            runCatching { audio.stop() }
            audio.release()
            recorder = null
        }
    }

    private fun uploadLoop() {
        while (!closing) {
            val pcm = try {
                uploadQueue.poll(250, TimeUnit.MILLISECONDS)
            } catch (_: InterruptedException) {
                break
            } ?: continue
            if (!closing) DroidLinkTransport.postAudioChunk(this, token, pcm)
        }
    }

    private fun failCapture() {
        getSharedPreferences(DroidLinkTransport.PREFS, MODE_PRIVATE)
            .edit().putBoolean(PREF_AUDIO_RUNNING, false).apply()
        closing = true
        stopSelf()
    }

    private fun stopCapture() {
        closing = true
        runCatching { recorder?.stop() }
        uploadQueue.clear()
        captureThread?.interrupt()
        uploadThread?.interrupt()
        captureThread = null
        uploadThread = null
        getSharedPreferences(DroidLinkTransport.PREFS, MODE_PRIVATE)
            .edit().putBoolean(PREF_AUDIO_RUNNING, false).apply()
    }

    override fun onDestroy() {
        stopCapture()
        super.onDestroy()
    }
}
