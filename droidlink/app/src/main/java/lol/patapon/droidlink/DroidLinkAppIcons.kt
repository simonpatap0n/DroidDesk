package lol.patapon.droidlink

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Rect
import android.graphics.drawable.Drawable
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream

object DroidLinkAppIcons {
    // Send launcher artwork at a size close to DroidDesk's rendered tile, so
    // high-DPI desktops do not have to enlarge a tiny thumbnail. Fall back to
    // smaller rasters when needed to stay within the relay's per-icon limit.
    private const val MAX_ENCODED_ICON_CHARS = 80_000
    private val ICON_SIZES = intArrayOf(320, 192, 128, 96)

    fun sendIcons(context: Context, token: String, packages: JSONArray) {
        val items = JSONArray()
        val packageManager = context.packageManager
        for (index in 0 until minOf(packages.length(), 10)) {
            val packageName = packages.optString(index)
            if (!packageName.matches(Regex("[A-Za-z0-9_.]{1,160}"))) continue
            runCatching {
                val application = packageManager.getApplicationInfo(packageName, 0)
                val drawable = packageManager.getApplicationIcon(application)
                val encoded = encode(drawable)
                if (encoded.isNotEmpty()) {
                    items.put(
                        JSONObject()
                            .put("package", packageName)
                            .put("label", packageManager.getApplicationLabel(application).toString().take(100))
                            .put("png", encoded),
                    )
                }
            }
        }
        post(context, token, JSONObject().put("type", "app_icons").put("items", items))
    }

    private fun encode(drawable: Drawable): String {
        val intrinsicWidth = drawable.intrinsicWidth.coerceAtLeast(1)
        val intrinsicHeight = drawable.intrinsicHeight.coerceAtLeast(1)
        for (iconSize in ICON_SIZES) {
            val bitmap = Bitmap.createBitmap(iconSize, iconSize, Bitmap.Config.ARGB_8888)
            val scale = minOf(iconSize.toFloat() / intrinsicWidth, iconSize.toFloat() / intrinsicHeight)
            val width = (intrinsicWidth * scale).toInt().coerceAtLeast(1)
            val height = (intrinsicHeight * scale).toInt().coerceAtLeast(1)
            val left = (iconSize - width) / 2
            val top = (iconSize - height) / 2
            drawable.bounds = Rect(left, top, left + width, top + height)
            drawable.draw(Canvas(bitmap))
            val output = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, output)
            bitmap.recycle()
            val encoded = Base64.encodeToString(output.toByteArray(), Base64.NO_WRAP)
            if (encoded.length <= MAX_ENCODED_ICON_CHARS) return encoded
        }
        return ""
    }

    private fun post(context: Context, token: String, payload: JSONObject) =
        DroidLinkTransport.post(context, token, "events", payload)
}
