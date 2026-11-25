package lol.patapon.droidlink

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

internal object DroidLinkTransport {
    const val PREFS = "droidlink"
    const val SESSION_TOKEN_KEY = "session_token"
    const val SESSION_ID_KEY = "relay_session_id"
    const val RELAY_URL_KEY = "relay_url"
    const val DEFAULT_RELAY_URL = "https://droidlink.patapon.lol"

    private fun baseUrl(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(RELAY_URL_KEY, DEFAULT_RELAY_URL)
            .orEmpty()
            .trimEnd('/')

    private fun sessionUrl(context: Context, route: String): String? {
        val sessionId = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(SESSION_ID_KEY, null)
            ?.takeIf { it.matches(Regex("[A-Za-z0-9_-]{20,64}")) }
            ?: return null
        return "${baseUrl(context)}/v1/sessions/$sessionId/$route"
    }

    fun post(context: Context, token: String, route: String, payload: JSONObject, timeoutMs: Int = 8_000): Int {
        val endpoint = sessionUrl(context, route) ?: return 0
        val connection = URL(endpoint).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.connectTimeout = minOf(4_000, timeoutMs)
            connection.readTimeout = timeoutMs
            connection.doOutput = true
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            connection.responseCode
        } catch (_: Exception) {
            0
        } finally {
            connection.disconnect()
        }
    }

    fun getCommands(context: Context, token: String): JSONArray? {
        val endpoint = sessionUrl(context, "commands")?.let { "$it?wait=18" } ?: return null
        val connection = URL(endpoint).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 4_000
            connection.readTimeout = 24_000
            connection.setRequestProperty("Authorization", "Bearer $token")
            if (connection.responseCode !in 200..299) return null
            val response = connection.inputStream.bufferedReader().use { it.readText() }
            JSONObject(response).optJSONArray("commands") ?: JSONArray()
        } catch (_: Exception) {
            null
        } finally {
            connection.disconnect()
        }
    }
}
