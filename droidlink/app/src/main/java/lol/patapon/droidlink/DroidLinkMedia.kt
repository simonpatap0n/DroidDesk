package lol.patapon.droidlink

import android.Manifest
import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.util.Base64
import android.util.Size
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL

object DroidLinkMedia {
    private const val PORT = 17891
    private const val MAX_RECENT_IMAGES = 2_000

    fun hasAccess(context: Context): Boolean {
        val permission = if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_IMAGES
        else Manifest.permission.READ_EXTERNAL_STORAGE
        val fullAccess = context.checkSelfPermission(permission) == android.content.pm.PackageManager.PERMISSION_GRANTED
        val selectedAccess = Build.VERSION.SDK_INT >= 34 && context.checkSelfPermission(
            Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED,
        ) == android.content.pm.PackageManager.PERMISSION_GRANTED
        return fullAccess || selectedAccess
    }

    fun sendRecentImages(context: Context, token: String) {
        if (!hasAccess(context)) {
            post(token, JSONObject().put("type", "photo_error").put("message", "Autorise l’accès aux photos dans DroidLink → Accès."))
            return
        }
        val rows = JSONArray()
        val legacyColumns = arrayOf(
            MediaStore.Images.Media._ID,
            MediaStore.Images.Media.DISPLAY_NAME,
            MediaStore.Images.Media.DATE_ADDED,
            MediaStore.Images.Media.SIZE,
            MediaStore.Images.Media.DATA,
        )
        val modernColumns = arrayOf(
            MediaStore.Images.Media._ID,
            MediaStore.Images.Media.DISPLAY_NAME,
            MediaStore.Images.Media.DATE_ADDED,
            MediaStore.Images.Media.SIZE,
            MediaStore.Images.Media.RELATIVE_PATH,
        )
        runCatching {
            context.contentResolver.query(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                if (Build.VERSION.SDK_INT >= 29) modernColumns else legacyColumns,
                null,
                null,
                "${MediaStore.Images.Media.DATE_ADDED} DESC",
            )?.use { cursor ->
                val idIndex = cursor.getColumnIndexOrThrow(MediaStore.Images.Media._ID)
                val nameIndex = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DISPLAY_NAME)
                val dateIndex = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DATE_ADDED)
                val sizeIndex = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.SIZE)
                val pathIndex = cursor.getColumnIndex(
                    if (Build.VERSION.SDK_INT >= 29) MediaStore.Images.Media.RELATIVE_PATH
                    else MediaStore.Images.Media.DATA,
                )
                var count = 0
                while (cursor.moveToNext() && count < MAX_RECENT_IMAGES) {
                    val storedPath = if (pathIndex >= 0) runCatching { cursor.getString(pathIndex).orEmpty() }.getOrDefault("") else ""
                    val displayName = cursor.getString(nameIndex).orEmpty().take(160)
                    val path = if (Build.VERSION.SDK_INT >= 29 && storedPath.isNotBlank()) {
                        val directory = storedPath.trim('/').takeIf { it.isNotBlank() }?.let { "$it/" }.orEmpty()
                        "/storage/emulated/0/$directory$displayName"
                    } else {
                        storedPath
                    }
                    rows.put(
                        JSONObject()
                            .put("id", cursor.getLong(idIndex))
                            .put("name", displayName)
                            .put("date", cursor.getLong(dateIndex))
                            .put("size", cursor.getLong(sizeIndex))
                            .put("path", path.take(800)),
                    )
                    count += 1
                }
            }
        }.onFailure {
            post(token, JSONObject().put("type", "photo_error").put("message", it.message.orEmpty().take(240)))
            return
        }
        post(token, JSONObject().put("type", "photo_list").put("items", rows).put("count", rows.length()))
    }

    fun sendThumbnail(context: Context, token: String, id: Long) {
        if (!hasAccess(context)) {
            post(token, JSONObject().put("type", "photo_error").put("message", "Autorise l’accès aux photos dans DroidLink → Accès."))
            return
        }
        runCatching {
            val uri: Uri = Uri.withAppendedPath(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id.toString())
            val bitmap = if (Build.VERSION.SDK_INT >= 29) {
                context.contentResolver.loadThumbnail(uri, Size(180, 180), null)
            } else {
                @Suppress("DEPRECATION")
                MediaStore.Images.Thumbnails.getThumbnail(
                    context.contentResolver,
                    id,
                    MediaStore.Images.Thumbnails.MINI_KIND,
                    null,
                )
            }
            val output = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.JPEG, 72, output)
            bitmap.recycle()
            post(
                token,
                JSONObject()
                    .put("type", "photo_thumbnail")
                    .put("id", id)
                    .put("jpeg", Base64.encodeToString(output.toByteArray(), Base64.NO_WRAP)),
            )
        }.onFailure {
            post(token, JSONObject().put("type", "photo_thumbnail_error").put("id", id).put("message", it.message.orEmpty().take(200)))
        }
    }

    private fun post(token: String, payload: JSONObject) {
        val connection = URL("http://localhost:$PORT/events").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 8_000
            connection.readTimeout = 8_000
            connection.doOutput = true
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            connection.responseCode
        } catch (_: Exception) {
            // The phone keeps its gallery even when DroidDesk is offline.
        } finally {
            connection.disconnect()
        }
    }
}
