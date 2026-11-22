package lol.patapon.droidlink

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.CallLog
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

object DroidLinkCallLog {
    private const val PORT = 17891
    private const val MAX_RECENT_CALLS = 100

    fun hasAccess(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.READ_CALL_LOG) == PackageManager.PERMISSION_GRANTED

    fun sendRecentCalls(context: Context, token: String) {
        if (!hasAccess(context)) {
            post(token, JSONObject().put("type", "call_log_error").put("message", "Autorise le journal d’appels dans DroidLink → Accès."))
            return
        }
        val calls = JSONArray()
        val columns = arrayOf(
            CallLog.Calls.NUMBER,
            CallLog.Calls.CACHED_NAME,
            CallLog.Calls.TYPE,
            CallLog.Calls.DATE,
            CallLog.Calls.DURATION,
        )
        runCatching {
            context.contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                columns,
                null,
                null,
                "${CallLog.Calls.DATE} DESC",
            )?.use { cursor ->
                val numberIndex = cursor.getColumnIndexOrThrow(CallLog.Calls.NUMBER)
                val nameIndex = cursor.getColumnIndexOrThrow(CallLog.Calls.CACHED_NAME)
                val typeIndex = cursor.getColumnIndexOrThrow(CallLog.Calls.TYPE)
                val dateIndex = cursor.getColumnIndexOrThrow(CallLog.Calls.DATE)
                val durationIndex = cursor.getColumnIndexOrThrow(CallLog.Calls.DURATION)
                var count = 0
                while (cursor.moveToNext() && count < MAX_RECENT_CALLS) {
                    calls.put(
                        JSONObject()
                            .put("number", cursor.getString(numberIndex).orEmpty().take(40))
                            .put("name", cursor.getString(nameIndex).orEmpty().take(120))
                            .put("type", cursor.getInt(typeIndex))
                            .put("date", cursor.getLong(dateIndex))
                            .put("duration", cursor.getLong(durationIndex)),
                    )
                    count += 1
                }
            }
        }.onFailure {
            post(token, JSONObject().put("type", "call_log_error").put("message", it.message.orEmpty().take(240)))
            return
        }
        post(token, JSONObject().put("type", "call_log_list").put("items", calls))
    }

    private fun post(token: String, payload: JSONObject) {
        val connection = URL("http://localhost:$PORT/events").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 4_000
            connection.readTimeout = 4_000
            connection.doOutput = true
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            connection.responseCode
        } catch (_: Exception) {
            // Call history remains available in the phone app while the desktop is offline.
        } finally {
            connection.disconnect()
        }
    }
}
