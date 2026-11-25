package lol.patapon.droidlink

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.ContactsContract
import org.json.JSONArray
import org.json.JSONObject

internal object DroidLinkContacts {
    fun hasAccess(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED

    fun send(context: Context, token: String) {
        if (!hasAccess(context)) {
            DroidLinkTransport.post(
                context,
                token,
                "events",
                JSONObject().put("type", "contacts_error").put("message", "Autorise les contacts dans DroidLink → Accès."),
            )
            return
        }
        val fields = JSONArray()
        runCatching {
            val projection = arrayOf(
                ContactsContract.CommonDataKinds.Phone.CONTACT_ID,
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                ContactsContract.CommonDataKinds.Phone.NUMBER,
            )
            context.contentResolver.query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                projection,
                null,
                null,
                "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} COLLATE NOCASE ASC",
            )?.use { cursor ->
                val idIndex = cursor.getColumnIndexOrThrow(ContactsContract.CommonDataKinds.Phone.CONTACT_ID)
                val nameIndex = cursor.getColumnIndexOrThrow(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME)
                val numberIndex = cursor.getColumnIndexOrThrow(ContactsContract.CommonDataKinds.Phone.NUMBER)
                val grouped = linkedMapOf<String, JSONObject>()
                while (cursor.moveToNext() && grouped.size < 5_000) {
                    val id = cursor.getString(idIndex).orEmpty()
                    val name = cursor.getString(nameIndex).orEmpty().take(160).ifBlank { "Contact sans nom" }
                    val number = cursor.getString(numberIndex).orEmpty().take(80)
                    if (number.isBlank()) continue
                    val item = grouped.getOrPut(id.ifBlank { "$name:$number" }) {
                        JSONObject().put("id", id.take(80)).put("name", name).put("numbers", JSONArray())
                    }
                    val numbers = item.getJSONArray("numbers")
                    if ((0 until numbers.length()).none { numbers.optString(it) == number }) numbers.put(number)
                }
                grouped.values.forEach { fields.put(it) }
            }
        }.onFailure { error ->
            DroidLinkTransport.post(
                context,
                token,
                "events",
                JSONObject().put("type", "contacts_error").put("message", error.message.orEmpty().take(200)),
            )
            return
        }
        DroidLinkTransport.post(
            context,
            token,
            "events",
            JSONObject().put("type", "contacts_list").put("items", fields),
        )
    }
}
