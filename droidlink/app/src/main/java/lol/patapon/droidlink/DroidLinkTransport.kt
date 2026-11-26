package lol.patapon.droidlink

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.net.URI
import java.net.HttpURLConnection
import java.net.URL

internal object DroidLinkTransport {
    const val PREFS = "droidlink"
    const val SESSION_TOKEN_KEY = "session_token"
    const val SESSION_ID_KEY = "relay_session_id"
    const val RELAY_URL_KEY = "relay_url"

    fun relayUrl(context: Context): String = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .getString(RELAY_URL_KEY, "")
        .orEmpty()
        .trimEnd('/')

    fun normalizeRelayUrl(value: String): String? {
        val trimmed = value.trim()
        if (trimmed.isEmpty()) return null
        val candidate = if ("://" in trimmed) trimmed else "https://$trimmed"
        val uri = runCatching { URI(candidate) }.getOrNull() ?: return null
        val host = uri.host ?: return null
        if (uri.scheme != "https" || uri.userInfo != null || uri.query != null || uri.fragment != null) return null
        if (uri.path.isNotEmpty() && uri.path != "/") return null
        if (!host.contains('.') || host.any { it.isWhitespace() }) return null
        if (uri.port > 65_535) return null
        val port = uri.port.takeIf { it >= 0 }?.let { ":$it" }.orEmpty()
        return "https://${host.lowercase()}$port"
    }

    private fun baseUrl(context: Context): String =
        relayUrl(context)

    private fun sessionUrl(context: Context, route: String): String? {
        val base = baseUrl(context).takeIf { it.isNotBlank() } ?: return null
        val sessionId = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(SESSION_ID_KEY, null)
            ?.takeIf { it.matches(Regex("[A-Za-z0-9_-]{20,64}")) }
            ?: return null
        return "$base/v1/sessions/$sessionId/$route"
    }

    private fun sessionRoute(route: String): String = "/v1/sessions/{id}/${route.substringBefore('?')}"

    fun checkHealth(context: Context): Boolean {
        val base = baseUrl(context)
        if (base.isBlank()) return false
        val connection = URL("$base/healthz").openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 4_000
            connection.readTimeout = 5_000
            val status = connection.responseCode
            RelayDiagnostics.recordResponse("GET /healthz", status)
            status in 200..299
        } catch (error: Exception) {
            RelayDiagnostics.recordFailure("GET /healthz", error)
            false
        } finally {
            connection.disconnect()
        }
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
            val status = connection.responseCode
            RelayDiagnostics.recordResponse("POST ${sessionRoute(route)}", status)
            status
        } catch (error: Exception) {
            RelayDiagnostics.recordFailure("POST ${sessionRoute(route)}", error)
            0
        } finally {
            connection.disconnect()
        }
    }

    fun postCameraFrame(context: Context, token: String, jpeg: ByteArray, rotation: Int): Int {
        val endpoint = sessionUrl(context, "camera/frame") ?: return 0
        val connection = URL(endpoint).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 4_000
            connection.readTimeout = 8_000
            connection.doOutput = true
            connection.setFixedLengthStreamingMode(jpeg.size)
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("Content-Type", "image/jpeg")
            connection.setRequestProperty("X-Frame-Rotation", rotation.toString())
            connection.setRequestProperty("Connection", "keep-alive")
            connection.outputStream.use { it.write(jpeg) }
            val responseCode = connection.responseCode
            val response = if (responseCode in 200..399) connection.inputStream else connection.errorStream
            response?.use { stream ->
                val buffer = ByteArray(1024)
                var count = stream.read(buffer)
                while (count != -1) count = stream.read(buffer)
            }
            responseCode
        } catch (_: Exception) {
            0
        } finally {
            connection.disconnect()
        }
    }

    fun postAudioChunk(context: Context, token: String, pcm: ByteArray): Int {
        val endpoint = sessionUrl(context, "audio/chunk") ?: return 0
        val connection = URL(endpoint).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 3_000
            connection.readTimeout = 3_000
            connection.doOutput = true
            connection.setFixedLengthStreamingMode(pcm.size)
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("Content-Type", "application/octet-stream")
            connection.setRequestProperty("X-Audio-Sample-Rate", "16000")
            connection.setRequestProperty("X-Audio-Channels", "1")
            connection.setRequestProperty("X-Audio-Format", "s16le")
            connection.outputStream.use { it.write(pcm) }
            val responseCode = connection.responseCode
            val response = if (responseCode in 200..399) connection.inputStream else connection.errorStream
            response?.use { stream ->
                val buffer = ByteArray(256)
                var count = stream.read(buffer)
                while (count != -1) count = stream.read(buffer)
            }
            responseCode
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
            val status = connection.responseCode
            RelayDiagnostics.recordResponse("GET ${sessionRoute("commands")}", status)
            if (status !in 200..299) return null
            val response = connection.inputStream.bufferedReader().use { it.readText() }
            JSONObject(response).optJSONArray("commands") ?: JSONArray()
        } catch (error: Exception) {
            RelayDiagnostics.recordFailure("GET ${sessionRoute("commands")}", error)
            null
        } finally {
            connection.disconnect()
        }
    }
}
