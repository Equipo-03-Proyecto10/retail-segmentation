package online.maxthecoder.mosaiq.floor.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/**
 * The selected customer's segment, exactly as segments-service states it, and
 * their recommendations with reasons (F17-05). Tapping one opens the product
 * and counts one RECO_CLICK.
 */
@Composable
fun CustomerInsights(model: CustomerInsightsViewModel, onOpenProduct: (Int) -> Unit) {
    val state by model.state.collectAsStateWithLifecycle()
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Segment", style = MaterialTheme.typography.titleMedium)
        val assignment = state.assignment
        val assignmentError = state.assignmentError
        when {
            state.loading -> Text("Loading the segment and recommendations…")
            assignment != null ->
                Text(
                    CustomerInsightsViewModel.segmentLine(assignment),
                    modifier = Modifier.testTag("segment")
                )
            assignmentError != null -> ErrorPanel(assignmentError)
        }
        Text("Recommendations", style = MaterialTheme.typography.titleMedium)
        val result = state.recommendations
        val recommendationsError = state.recommendationsError
        when {
            recommendationsError != null -> ErrorPanel(recommendationsError)
            result != null && state.items.isEmpty() ->
                Text(CustomerInsightsViewModel.whyNone(result.status), modifier = Modifier.testTag("why-none"))
        }
        state.items.forEach { shown ->
            Column(
                modifier =
                Modifier
                    .fillMaxWidth()
                    .clickable { model.open(shown, openProduct = onOpenProduct) }
                    .padding(vertical = 4.dp)
                    .testTag("recommendation-${shown.rank}")
            ) {
                Text("${shown.rank}. ${shown.name}", style = MaterialTheme.typography.bodyLarge)
                Text("${shown.item.quantityOnHand} on hand at their usual store")
                shown.reasons.forEach { Text("· $it", style = MaterialTheme.typography.bodySmall) }
            }
        }
        state.clickNote?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        state.customerId?.let { id -> OutlinedButton(onClick = { model.load(id) }) { Text("Refresh") } }
    }
}
