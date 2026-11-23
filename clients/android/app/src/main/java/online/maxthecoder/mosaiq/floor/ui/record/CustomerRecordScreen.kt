package online.maxthecoder.mosaiq.floor.ui.record

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.auth.RecordPart
import online.maxthecoder.mosaiq.floor.auth.RecordWriters
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.net.PurposeConsent
import online.maxthecoder.mosaiq.floor.ui.CONSENT_PURPOSES
import online.maxthecoder.mosaiq.floor.ui.CustomerRecordViewModel
import online.maxthecoder.mosaiq.floor.ui.EnrolState
import online.maxthecoder.mosaiq.floor.ui.GRANTED
import online.maxthecoder.mosaiq.floor.ui.HistoryState
import online.maxthecoder.mosaiq.floor.ui.PROGRAMME_DEFAULT
import online.maxthecoder.mosaiq.floor.ui.PartWrite
import online.maxthecoder.mosaiq.floor.ui.RecordState
import online.maxthecoder.mosaiq.floor.ui.WITHDRAWN
import online.maxthecoder.mosaiq.floor.ui.components.MqBadge
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqListRow
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqSection
import online.maxthecoder.mosaiq.floor.ui.components.MqTextField
import online.maxthecoder.mosaiq.floor.ui.components.MqTone
import online.maxthecoder.mosaiq.floor.ui.formatDate
import online.maxthecoder.mosaiq.floor.ui.humanize
import online.maxthecoder.mosaiq.floor.ui.sale.channelLabel
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** The typed values of the new-customer form; the screen keeps them across recreation. */
data class EnrolFields(val name: String = "", val email: String = "", val phone: String = "")

/** Registering a customer and asking their consent per purpose (F27-09). [onRegistered] gets the new id. */
@Composable
fun NewCustomerScreen(model: CustomerRecordViewModel, onRegistered: (String) -> Unit) {
    LaunchedEffect(Unit) { model.loadChannels() }
    val state by model.enrol.collectAsStateWithLifecycle()
    var name by rememberSaveable { mutableStateOf("") }
    var email by rememberSaveable { mutableStateOf("") }
    var phone by rememberSaveable { mutableStateOf("") }
    NewCustomerBody(
        fields = EnrolFields(name, email, phone),
        onFields = {
            name = it.name
            email = it.email
            phone = it.phone
        },
        state = state,
        onLoadChannels = model::loadChannels,
        onChooseChannel = model::chooseChannel,
        onAnswer = model::answerConsent,
        onSubmit = { model.submit(name, email, phone, onRegistered) }
    )
}

/** Every state of the new-customer form from plain values, for previews and tests. */
@Composable
fun NewCustomerBody(
    fields: EnrolFields,
    onFields: (EnrolFields) -> Unit,
    state: EnrolState,
    onLoadChannels: () -> Unit,
    onChooseChannel: (Int) -> Unit,
    onAnswer: (String, String) -> Unit,
    onSubmit: () -> Unit
) {
    val registered = state.customer != null
    val canSubmit =
        state.consentAnswered && !state.sending &&
            (registered || (fields.name.isNotBlank() && state.selectedChannelId != null))
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(Spacing.Lg)
    ) {
        Text(
            stringResource(R.string.record_new_title),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        state.error?.let { MqErrorPanel(it) }
        if (state.timeoutAdvice) {
            Text(stringResource(R.string.sale_register_unknown), style = MaterialTheme.typography.bodyMedium)
        }
        if (registered) {
            MqBadge(
                stringResource(R.string.record_new_registered, state.customer!!.name),
                tone = MqTone.Warning,
                modifier = Modifier.testTag("enrol-registered")
            )
        }
        MqSection(stringResource(R.string.record_new_details)) {
            MqTextField(
                fields.name,
                { onFields(fields.copy(name = it)) },
                stringResource(R.string.sale_field_name),
                enabled = !registered,
                supportingText = stringResource(R.string.sale_field_required),
                errorText = fieldError(state.error, "name"),
                keyboardOptions =
                KeyboardOptions(capitalization = KeyboardCapitalization.Words, imeAction = ImeAction.Next)
            )
            MqTextField(
                fields.email,
                { onFields(fields.copy(email = it)) },
                stringResource(R.string.sale_field_email),
                enabled = !registered,
                errorText = fieldError(state.error, "email"),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Next)
            )
            MqTextField(
                fields.phone,
                { onFields(fields.copy(phone = it)) },
                stringResource(R.string.sale_field_phone),
                enabled = !registered,
                errorText = fieldError(state.error, "phone"),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone, imeAction = ImeAction.Done)
            )
        }
        MqSection(stringResource(R.string.sale_register_channel)) {
            when {
                state.channels.isEmpty() && state.loadingChannels ->
                    MqLoading(stringResource(R.string.sale_channels_loading))
                state.channels.isEmpty() ->
                    MqButton(
                        stringResource(R.string.sale_channels_load),
                        onLoadChannels,
                        variant = MqButtonVariant.Secondary
                    )
                else ->
                    Column(Modifier.selectableGroup()) {
                        state.channels.forEach { channel ->
                            Choice(
                                label = channelLabel(channel.name),
                                selected = state.selectedChannelId == channel.channelId,
                                enabled = !registered,
                                onClick = { onChooseChannel(channel.channelId) }
                            )
                        }
                    }
            }
        }
        MqSection(stringResource(R.string.record_consent_title)) {
            Text(stringResource(R.string.record_new_consent_help), style = MaterialTheme.typography.bodyMedium)
            CONSENT_PURPOSES.forEach { purpose ->
                PurposeQuestion(purpose, state.consent[purpose], enabled = !state.sending) { onAnswer(purpose, it) }
            }
        }
        MqButton(
            stringResource(if (registered) R.string.record_new_retry_consent else R.string.sale_register_submit),
            onSubmit,
            enabled = canSubmit || state.sending,
            loading = state.sending,
            fullWidth = true,
            modifier = Modifier.testTag("enrol-submit")
        )
        if (!state.consentAnswered) {
            Text(
                stringResource(R.string.record_new_consent_missing),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}

/** A customer's record: consent, benefits and preferences, who may change each, and their history. */
@Composable
fun CustomerRecordScreen(model: CustomerRecordViewModel, customerId: String, onBack: () -> Unit) {
    LaunchedEffect(customerId) { model.open(customerId) }
    val state by model.record.collectAsStateWithLifecycle()
    val history by model.history.collectAsStateWithLifecycle()
    RecordBody(
        state = state,
        history = history,
        userId = model.userId,
        actions =
        RecordActions(
            onRetry = model::retry,
            onChooseConsent = model::chooseConsent,
            onSaveConsent = model::saveConsent,
            onToggleChannel = model::toggleChannel,
            onSaveChannels = model::saveChannels,
            onToggleInterest = model::toggleInterest,
            onSaveInterests = model::saveInterests,
            onToggleHistory = model::toggleHistory,
            onRetryHistory = model::loadHistory,
            onBack = onBack
        )
    )
}

class RecordActions(
    val onRetry: () -> Unit = {},
    val onChooseConsent: (String, String) -> Unit = { _, _ -> },
    val onSaveConsent: () -> Unit = {},
    val onToggleChannel: (Int) -> Unit = {},
    val onSaveChannels: () -> Unit = {},
    val onToggleInterest: (Int) -> Unit = {},
    val onSaveInterests: () -> Unit = {},
    val onToggleHistory: () -> Unit = {},
    val onRetryHistory: () -> Unit = {},
    val onBack: () -> Unit = {}
)

/** Every state of the record from plain values, for previews and tests. */
@Composable
fun RecordBody(state: RecordState, history: HistoryState, userId: String?, actions: RecordActions) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(Spacing.Lg)
    ) {
        val customer = state.customer
        when {
            state.error != null -> MqErrorPanel(state.error, onRetry = actions.onRetry)
            !state.ready || customer == null -> MqLoading(stringResource(R.string.record_loading))
            else -> {
                Text(
                    customer.name,
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.semantics { heading() }
                )
                customer.email?.let { MqListRow(stringResource(R.string.identify_email), supporting = it) }
                customer.phone?.let { MqListRow(stringResource(R.string.identify_phone), supporting = it) }
                ConsentPart(state, actions)
                MqSection(stringResource(R.string.record_benefits_title), Modifier.testTag("record-benefits")) {
                    Text(stringResource(R.string.record_benefits_none), style = MaterialTheme.typography.bodyMedium)
                }
                ChannelsPart(state, actions)
                InterestsPart(state, actions)
                HistoryPart(state, history, userId, actions)
            }
        }
        MqButton(stringResource(R.string.record_back), actions.onBack, variant = MqButtonVariant.Quiet)
    }
}

@Composable
private fun ConsentPart(state: RecordState, actions: RecordActions) {
    val write = state.write(RecordPart.CONSENT)
    MqSection(stringResource(R.string.record_consent_title), Modifier.testTag("record-consent")) {
        WhoMayChange(RecordPart.CONSENT)
        state.consent?.purposes.orEmpty().forEach { held ->
            PurposeQuestion(
                held.purpose,
                state.consentDraft[held.purpose],
                enabled = !write.saving,
                held = held
            ) { actions.onChooseConsent(held.purpose, it) }
        }
        SaveRow(
            label = stringResource(R.string.record_consent_save),
            write = write,
            enabled = state.consentChanges.isNotEmpty(),
            tag = "record-consent-save",
            onSave = actions.onSaveConsent
        )
    }
}

@Composable
private fun ChannelsPart(state: RecordState, actions: RecordActions) {
    MqSection(stringResource(R.string.record_channels_title), Modifier.testTag("record-channels")) {
        WhoMayChange(RecordPart.PREFERRED_CHANNELS)
        val write = state.write(RecordPart.PREFERRED_CHANNELS)
        state.channels.forEach { channel ->
            Check(channelLabel(channel.name), channel.channelId in state.channelsDraft, !write.saving) {
                actions.onToggleChannel(channel.channelId)
            }
        }
        SaveRow(
            stringResource(R.string.record_channels_save),
            write,
            state.channelsChanged,
            "record-channels-save",
            actions.onSaveChannels
        )
    }
}

@Composable
private fun InterestsPart(state: RecordState, actions: RecordActions) {
    MqSection(stringResource(R.string.record_interests_title), Modifier.testTag("record-interests")) {
        WhoMayChange(RecordPart.INTERESTS)
        val write = state.write(RecordPart.INTERESTS)
        state.categories.forEach { category ->
            Check(category.name, category.categoryId in state.interestsDraft, !write.saving) {
                actions.onToggleInterest(category.categoryId)
            }
        }
        SaveRow(
            stringResource(R.string.sale_interests_save),
            write,
            state.interestsChanged,
            "record-interests-save",
            actions.onSaveInterests
        )
    }
}

@Composable
private fun HistoryPart(state: RecordState, history: HistoryState, userId: String?, actions: RecordActions) {
    MqSection(stringResource(R.string.record_history_title), Modifier.testTag("record-history")) {
        MqButton(
            stringResource(if (history.open) R.string.record_history_hide else R.string.record_history_show),
            actions.onToggleHistory,
            variant = MqButtonVariant.Secondary,
            modifier = Modifier.testTag("record-history-toggle")
        )
        if (!history.open) return@MqSection
        when {
            history.loading -> MqLoading(stringResource(R.string.record_history_loading))
            history.error != null -> MqErrorPanel(history.error, onRetry = actions.onRetryHistory)
            history.consent.isEmpty() && history.changes.isEmpty() ->
                Text(stringResource(R.string.record_history_empty), style = MaterialTheme.typography.bodyMedium)
        }
        if (history.loading) return@MqSection
        history.consent.forEach { row ->
            MqListRow(
                headline = stringResource(
                    R.string.record_history_consent,
                    purposeLabel(row.purpose),
                    stateLabel(row.decision)
                ),
                supporting = when {
                    row.basis == PROGRAMME_DEFAULT -> stringResource(
                        R.string.record_history_default,
                        formatDate(row.recordedAt)
                    )
                    row.recordedBy != null && row.recordedBy == userId ->
                        stringResource(R.string.record_history_by_you, formatDate(row.recordedAt))
                    else -> formatDate(row.recordedAt)
                },
                divider = true
            )
        }
        history.changes.forEach { change ->
            val isChannels = change.preferenceSet == CHANNEL_SET
            val nameOf: (Int) -> String? = { id ->
                if (isChannels) {
                    state.channels.firstOrNull { it.channelId == id }?.name?.let(::humanize)
                } else {
                    state.categories.firstOrNull { it.categoryId == id }?.name
                }
            }
            val names: (List<Int>) -> String = { ids -> namesOf(ids, nameOf) }
            val set =
                stringResource(if (isChannels) R.string.record_channels_title else R.string.record_interests_title)
            val none = stringResource(R.string.record_history_none)
            MqListRow(
                headline = stringResource(R.string.record_history_change, set),
                supporting =
                stringResource(
                    if (change.changedBy == userId) {
                        R.string.record_history_change_by_you
                    } else {
                        R.string.record_history_change_detail
                    },
                    names(change.beforeIds).ifEmpty { none },
                    names(change.afterIds).ifEmpty { none },
                    formatDate(change.changedAt)
                ),
                divider = true
            )
        }
    }
}

private const val CHANNEL_SET = "PREFERRED_CHANNELS"

private fun namesOf(ids: List<Int>, name: (Int) -> String?): String = ids.joinToString { name(it) ?: "#$it" }

/** One purpose with a yes and a no, neither chosen until the officer chooses ([answer] null). */
@Composable
private fun PurposeQuestion(
    purpose: String,
    answer: String?,
    enabled: Boolean,
    held: PurposeConsent? = null,
    onAnswer: (String) -> Unit
) {
    Column(Modifier.testTag("purpose-$purpose"), verticalArrangement = Arrangement.spacedBy(Spacing.Xs)) {
        Text(purposeLabel(purpose), style = MaterialTheme.typography.titleSmall)
        Text(
            purposeHelp(purpose),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        if (held != null) {
            val default = held.basis == PROGRAMME_DEFAULT
            MqBadge(
                if (default) {
                    stringResource(R.string.record_consent_default, stateLabel(held.state))
                } else {
                    stateLabel(held.state)
                },
                tone = if (held.state == GRANTED && !default) MqTone.Success else MqTone.Neutral
            )
        }
        Row(Modifier.fillMaxWidth().selectableGroup(), horizontalArrangement = Arrangement.spacedBy(Spacing.Lg)) {
            listOf(
                GRANTED to R.string.record_consent_yes,
                WITHDRAWN to R.string.record_consent_no
            ).forEach { (value, label) ->
                Choice(
                    label = stringResource(label),
                    selected = answer == value,
                    enabled = enabled,
                    modifier = Modifier.weight(1f).testTag("consent-$purpose-$value"),
                    onClick = { onAnswer(value) }
                )
            }
        }
    }
}

@Composable
private fun Choice(
    label: String,
    selected: Boolean,
    enabled: Boolean,
    modifier: Modifier = Modifier.fillMaxWidth(),
    onClick: () -> Unit
) {
    Row(
        modifier.heightIn(min = Spacing.X5l)
            .selectable(selected = selected, enabled = enabled, role = Role.RadioButton, onClick = onClick),
        horizontalArrangement = Arrangement.spacedBy(Spacing.Sm),
        verticalAlignment = Alignment.CenterVertically
    ) {
        RadioButton(selected = selected, onClick = null, enabled = enabled)
        Text(label, style = MaterialTheme.typography.bodyLarge)
    }
}

@Composable
private fun Check(label: String, checked: Boolean, enabled: Boolean, onToggle: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = Spacing.X5l)
            .toggleable(value = checked, enabled = enabled, role = Role.Checkbox, onValueChange = { onToggle() }),
        horizontalArrangement = Arrangement.spacedBy(Spacing.Sm),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Checkbox(checked = checked, onCheckedChange = null, enabled = enabled)
        Text(label, style = MaterialTheme.typography.bodyLarge)
    }
}

@Composable
private fun SaveRow(label: String, write: PartWrite, enabled: Boolean, tag: String, onSave: () -> Unit) {
    write.error?.let { MqErrorPanel(it) }
    MqButton(
        label,
        onSave,
        enabled = enabled || write.saving,
        loading = write.saving,
        variant = MqButtonVariant.Secondary,
        modifier = Modifier.testTag(tag)
    )
    if (write.saved) {
        Text(
            stringResource(R.string.record_saved),
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }
        )
    }
}

/** "Changed by: Loyalty officer, Store manager, Administrator", from [RecordWriters]. */
@Composable
private fun WhoMayChange(part: RecordPart) {
    val names = RecordWriters.rolesFor(part).map { roleLabel(it) }
    Text(
        stringResource(R.string.record_who_may_change, names.joinToString()),
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.testTag("who-${part.name}")
    )
}

@Composable
private fun roleLabel(role: String): String = when (role) {
    "LOYALTY_OFFICER" -> stringResource(R.string.role_loyalty_officer)
    "STORE_MANAGER" -> stringResource(R.string.role_store_manager)
    "ADMIN" -> stringResource(R.string.role_admin)
    else -> humanize(role)
}

@Composable
private fun purposeLabel(purpose: String): String = when (purpose) {
    "PERSONALIZATION" -> stringResource(R.string.consent_personalization)
    "MARKETING_CONTACT" -> stringResource(R.string.consent_marketing_contact)
    "PROFILING" -> stringResource(R.string.consent_profiling)
    else -> humanize(purpose)
}

@Composable
private fun purposeHelp(purpose: String): String = when (purpose) {
    "PERSONALIZATION" -> stringResource(R.string.consent_personalization_help)
    "MARKETING_CONTACT" -> stringResource(R.string.consent_marketing_contact_help)
    "PROFILING" -> stringResource(R.string.consent_profiling_help)
    else -> ""
}

@Composable
private fun stateLabel(state: String): String = when (state) {
    GRANTED -> stringResource(R.string.consent_granted)
    WITHDRAWN -> stringResource(R.string.consent_withdrawn)
    else -> stringResource(R.string.consent_not_recorded)
}

/** The service's refusal for one field, in words. */
private fun fieldError(error: ApiError?, field: String): String? =
    error?.details.orEmpty().firstOrNull { it.field == field }?.code?.let(::humanize)
