package online.maxthecoder.mosaiq.floor.ui.outbox

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.outbox.OutboxRow
import online.maxthecoder.mosaiq.floor.outbox.RowState
import online.maxthecoder.mosaiq.floor.outbox.refusalAdvice
import online.maxthecoder.mosaiq.floor.ui.ErrorPanel
import online.maxthecoder.mosaiq.floor.ui.FloorViewModel
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

@Composable
fun OutboxList(model: FloorViewModel) {
    val rows by model.open.collectAsStateWithLifecycle()
    val userId by model.userId.collectAsStateWithLifecycle()
    if (rows.isEmpty()) {
        Text("Nothing is waiting, and nothing was refused.")
        return
    }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(Spacing.Md)) {
        items(rows, key = { it.id }) { row ->
            Column(verticalArrangement = Arrangement.spacedBy(Spacing.Xs)) {
                Text("${row.operation} · ${row.clientId.take(8)}", style = MaterialTheme.typography.titleSmall)
                when {
                    row.state == RowState.REFUSED -> {
                        ErrorPanel(row.asError())
                        refusalAdvice(row.code)?.let { Text(it) }
                        OutlinedButton(onClick = { model.dismiss(row) }) { Text("Dismiss") }
                    }
                    row.userId != userId -> Text("Waiting for the user who made it to sign in again.")
                    else -> Text("Waiting to be sent (${row.attempts} attempt(s) so far).")
                }
            }
        }
    }
}

internal fun OutboxRow.asError(): ApiError = ApiError(
    status = status ?: 0,
    code = code ?: "refused",
    message = message ?: "The service refused this write.",
    correlationId = correlationId
)
