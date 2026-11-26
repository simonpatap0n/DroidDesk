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
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.media.Image
import android.media.ImageReader
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.SystemClock
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs

class DroidLinkCameraService : Service() {
    companion object {
        const val ACTION_START = "lol.patapon.droidlink.camera.START"
        const val ACTION_STOP = "lol.patapon.droidlink.camera.STOP"
        private const val CHANNEL_ID = "droidlink_camera"
        private const val NOTIFICATION_ID = 7310
        private const val PREF_CAMERA_RUNNING = "camera_share_running"

        fun start(context: Context) {
            val intent = Intent(context, DroidLinkCameraService::class.java).setAction(ACTION_START)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent)
            else context.startService(intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, DroidLinkCameraService::class.java))
            context.getSharedPreferences(DroidLinkTransport.PREFS, Context.MODE_PRIVATE)
                .edit().putBoolean(PREF_CAMERA_RUNNING, false).apply()
        }

        fun isRunning(context: Context): Boolean = context
            .getSharedPreferences(DroidLinkTransport.PREFS, Context.MODE_PRIVATE)
            .getBoolean(PREF_CAMERA_RUNNING, false)
    }

    private val uploads = Executors.newSingleThreadExecutor()
    private val uploadInFlight = AtomicBoolean(false)
    private var cameraThread: HandlerThread? = null
    private var cameraHandler: Handler? = null
    private var cameraDevice: CameraDevice? = null
    private var captureSession: CameraCaptureSession? = null
    private var imageReader: ImageReader? = null
    private var frameRotation = 0
    private var lastFrameAt = 0L
    private var token = ""
    @Volatile private var closing = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopCapture()
            reportStatus(false, "Partage de caméra arrêté.")
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf(startId)
            return START_NOT_STICKY
        }

        token = getSharedPreferences(DroidLinkTransport.PREFS, MODE_PRIVATE)
            .getString(DroidLinkTransport.SESSION_TOKEN_KEY, null).orEmpty()
        if (token.isBlank()) {
            stopSelf(startId)
            return START_NOT_STICKY
        }
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            reportStatus(false, "Autorise l’accès à la caméra dans DroidLink.")
            stopSelf(startId)
            return START_NOT_STICKY
        }

        createNotificationChannel()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                cameraNotification(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA,
            )
        } else {
            startForeground(NOTIFICATION_ID, cameraNotification())
        }
        closing = false
        getSharedPreferences(DroidLinkTransport.PREFS, MODE_PRIVATE)
            .edit().putBoolean(PREF_CAMERA_RUNNING, true).apply()
        reportStatus(true, "La caméra du téléphone est partagée avec DroidDesk.")
        if (cameraDevice == null) openCamera()
        return START_NOT_STICKY
    }

    private fun createNotificationChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Partage de caméra", NotificationManager.IMPORTANCE_LOW)
                .apply { description = "Indique quand DroidLink transmet la caméra à DroidDesk." },
        )
    }

    private fun cameraNotification(): Notification {
        val stopIntent = PendingIntent.getService(
            this,
            NOTIFICATION_ID,
            Intent(this, DroidLinkCameraService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(applicationInfo.icon)
            .setContentTitle("Caméra partagée avec DroidDesk")
            .setContentText("La caméra est active. Touche Arrêter pour couper le flux.")
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .addAction(0, "Arrêter", stopIntent)
            .build()
    }

    private fun openCamera() {
        try {
            val manager = getSystemService(CameraManager::class.java)
            val cameras = manager.cameraIdList.mapNotNull { id ->
                val characteristics = manager.getCameraCharacteristics(id)
                val facing = characteristics.get(CameraCharacteristics.LENS_FACING)
                val map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                val sizes = map?.getOutputSizes(ImageFormat.JPEG)?.toList().orEmpty()
                if (sizes.isEmpty()) null else Triple(id, facing, sizes)
            }
            val selected = cameras.firstOrNull { it.second == CameraCharacteristics.LENS_FACING_FRONT }
                ?: cameras.firstOrNull { it.second == CameraCharacteristics.LENS_FACING_BACK }
                ?: cameras.firstOrNull()
                ?: throw IllegalStateException("Aucune caméra compatible n’a été trouvée.")
            val cameraId = selected.first
            val size = selected.third.minBy { abs(it.width - 640) + abs(it.height - 480) }
            val characteristics = manager.getCameraCharacteristics(cameraId)
            frameRotation = characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0
            val reader = ImageReader.newInstance(size.width, size.height, ImageFormat.JPEG, 2)
            imageReader = reader
            cameraThread = HandlerThread("DroidLinkCamera").also { it.start() }
            cameraHandler = Handler(cameraThread!!.looper)
            reader.setOnImageAvailableListener({ source -> onImageAvailable(source) }, cameraHandler)
            manager.openCamera(cameraId, object : CameraDevice.StateCallback() {
                override fun onOpened(camera: CameraDevice) {
                    if (closing) {
                        camera.close()
                        return
                    }
                    cameraDevice = camera
                    createCaptureSession(camera, reader)
                }

                override fun onDisconnected(camera: CameraDevice) {
                    camera.close()
                    cameraDevice = null
                    reportStatus(false, "La caméra du téléphone s’est déconnectée.")
                    stopSelf()
                }

                override fun onError(camera: CameraDevice, error: Int) {
                    camera.close()
                    cameraDevice = null
                    reportStatus(false, "Android n’a pas pu ouvrir la caméra (erreur $error).")
                    stopSelf()
                }
            }, cameraHandler)
        } catch (error: Exception) {
            reportStatus(false, error.message ?: "Impossible d’ouvrir la caméra du téléphone.")
            stopSelf()
        }
    }

    private fun createCaptureSession(camera: CameraDevice, reader: ImageReader) {
        try {
            camera.createCaptureSession(
                listOf(reader.surface),
                object : CameraCaptureSession.StateCallback() {
                    override fun onConfigured(session: CameraCaptureSession) {
                        if (closing) {
                            session.close()
                            return
                        }
                        captureSession = session
                        try {
                            val request = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
                                addTarget(reader.surface)
                                set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO)
                                set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO)
                                set(CaptureRequest.JPEG_QUALITY, 58.toByte())
                            }.build()
                            session.setRepeatingRequest(request, null, cameraHandler)
                        } catch (error: Exception) {
                            reportStatus(false, error.message ?: "Impossible de démarrer le flux caméra.")
                            stopSelf()
                        }
                    }

                    override fun onConfigureFailed(session: CameraCaptureSession) {
                        reportStatus(false, "Android a refusé la configuration du flux caméra.")
                        stopSelf()
                    }
                },
                cameraHandler,
            )
        } catch (error: Exception) {
            reportStatus(false, error.message ?: "Impossible de configurer le flux caméra.")
            stopSelf()
        }
    }

    private fun onImageAvailable(reader: ImageReader) {
        val image = try {
            reader.acquireLatestImage()
        } catch (_: Exception) {
            null
        } ?: return
        val now = SystemClock.elapsedRealtime()
        if (now - lastFrameAt < 100 || uploadInFlight.get()) {
            image.close()
            return
        }
        lastFrameAt = now
        val jpeg = try {
            image.toJpeg()
        } catch (_: Exception) {
            null
        } finally {
            image.close()
        } ?: return
        if (!uploadInFlight.compareAndSet(false, true)) return
        uploads.execute {
            try {
                if (!closing) DroidLinkTransport.postCameraFrame(this, token, jpeg, frameRotation)
            } finally {
                uploadInFlight.set(false)
            }
        }
    }

    private fun Image.toJpeg(): ByteArray? {
        if (format != ImageFormat.JPEG || planes.isEmpty()) return null
        val buffer = planes[0].buffer
        if (!buffer.hasRemaining()) return null
        return ByteArray(buffer.remaining()).also(buffer::get)
    }

    private fun reportStatus(running: Boolean, message: String) {
        getSharedPreferences(DroidLinkTransport.PREFS, MODE_PRIVATE)
            .edit().putBoolean(PREF_CAMERA_RUNNING, running).apply()
        val currentToken = token.ifBlank {
            getSharedPreferences(DroidLinkTransport.PREFS, MODE_PRIVATE)
                .getString(DroidLinkTransport.SESSION_TOKEN_KEY, null).orEmpty()
        }
        if (currentToken.isBlank()) return
        val event = JSONObject()
            .put("type", "camera_status")
            .put("running", running)
            .put("message", message.take(240))
        Thread { DroidLinkTransport.post(this, currentToken, "events", event) }.start()
    }

    private fun stopCapture() {
        closing = true
        runCatching { captureSession?.stopRepeating() }
        runCatching { captureSession?.close() }
        captureSession = null
        runCatching { cameraDevice?.close() }
        cameraDevice = null
        runCatching { imageReader?.close() }
        imageReader = null
        cameraThread?.quitSafely()
        cameraThread = null
        cameraHandler = null
        getSharedPreferences(DroidLinkTransport.PREFS, MODE_PRIVATE)
            .edit().putBoolean(PREF_CAMERA_RUNNING, false).apply()
    }

    override fun onDestroy() {
        val wasRunning = isRunning(this)
        stopCapture()
        if (wasRunning) reportStatus(false, "Partage de caméra arrêté.")
        uploads.shutdown()
        super.onDestroy()
    }
}
