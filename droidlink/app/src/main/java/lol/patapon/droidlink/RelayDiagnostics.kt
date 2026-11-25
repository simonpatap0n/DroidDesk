package lol.patapon.droidlink

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

internal data class RelayLogEntry(
    val timestampMs: Long,
    val message: String,
    val success: Boolean?,
)

internal data class RelayDiagnosticsState(
    val reachable: Boolean? = null,
    val entries: List<RelayLogEntry> = emptyList(),
)

internal object RelayDiagnostics {
    private const val MAX_ENTRIES = 40
    private val mutableState = MutableStateFlow(RelayDiagnosticsState())
    val state: StateFlow<RelayDiagnosticsState> = mutableState.asStateFlow()

    @Synchronized
    fun recordResponse(route: String, statusCode: Int) {
        val reachable = statusCode in 200..499
        addEntry("$route · HTTP $statusCode", reachable)
        mutableState.value = mutableState.value.copy(reachable = reachable)
    }

    @Synchronized
    fun recordFailure(route: String, error: Throwable) {
        val detail = (error.message ?: error.javaClass.simpleName)
            .replace('\n', ' ')
            .take(120)
        addEntry("$route · ${detail.ifBlank { "échec réseau" }}", false)
        mutableState.value = mutableState.value.copy(reachable = false)
    }

    @Synchronized
    fun recordNote(message: String, success: Boolean?) {
        addEntry(message.take(160), success)
    }

    private fun addEntry(message: String, success: Boolean?) {
        val next = RelayLogEntry(System.currentTimeMillis(), message, success)
        mutableState.value = mutableState.value.copy(entries = (listOf(next) + mutableState.value.entries).take(MAX_ENTRIES))
    }
}
