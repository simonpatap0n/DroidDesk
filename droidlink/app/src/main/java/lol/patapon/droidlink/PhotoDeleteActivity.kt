package lol.patapon.droidlink

import android.content.ContentUris
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import androidx.activity.ComponentActivity
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import org.json.JSONObject

class PhotoDeleteActivity : ComponentActivity() {
    private val deleteRequest = registerForActivityResult(
        ActivityResultContracts.StartIntentSenderForResult(),
    ) { result ->
        report(result.resultCode == RESULT_OK, if (result.resultCode == RESULT_OK) "Photo supprimée." else "Suppression annulée sur le téléphone.")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val photoId = intent.getLongExtra(EXTRA_PHOTO_ID, -1L)
        val token = getSharedPreferences(DroidLinkTransport.PREFS, MODE_PRIVATE)
            .getString(DroidLinkTransport.SESSION_TOKEN_KEY, null)
        if (photoId <= 0L || token.isNullOrBlank()) {
            finish()
            return
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            report(false, "La confirmation de suppression à distance nécessite Android 11 ou plus récent.")
            return
        }
        runCatching {
            val uri = ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, photoId)
            val request = MediaStore.createDeleteRequest(contentResolver, listOf(uri))
            deleteRequest.launch(IntentSenderRequest.Builder(request.intentSender).build())
        }.onFailure {
            report(false, it.message ?: "Impossible de demander la confirmation Android.")
        }
    }

    private fun report(success: Boolean, message: String) {
        val photoId = intent.getLongExtra(EXTRA_PHOTO_ID, -1L)
        val token = getSharedPreferences(DroidLinkTransport.PREFS, MODE_PRIVATE)
            .getString(DroidLinkTransport.SESSION_TOKEN_KEY, null)
        if (photoId > 0L && !token.isNullOrBlank()) {
            Thread {
                DroidLinkTransport.post(
                    this,
                    token,
                    "events",
                    JSONObject()
                        .put("type", "photo_delete_result")
                        .put("id", photoId)
                        .put("success", success)
                        .put("message", message.take(240)),
                )
                runOnUiThread { finish() }
            }.start()
        } else {
            finish()
        }
    }

    companion object {
        const val EXTRA_PHOTO_ID = "photo_id"
    }
}
