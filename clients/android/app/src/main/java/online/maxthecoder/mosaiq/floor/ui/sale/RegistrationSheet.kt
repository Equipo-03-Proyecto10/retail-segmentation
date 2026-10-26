package online.maxthecoder.mosaiq.floor.ui.sale

import android.util.Patterns
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.net.ApiError
import online.maxthecoder.mosaiq.floor.ui.RegistrationState
import online.maxthecoder.mosaiq.floor.ui.components.MqButton
import online.maxthecoder.mosaiq.floor.ui.components.MqButtonVariant
import online.maxthecoder.mosaiq.floor.ui.components.MqErrorPanel
import online.maxthecoder.mosaiq.floor.ui.components.MqLoading
import online.maxthecoder.mosaiq.floor.ui.components.MqTextField
import online.maxthecoder.mosaiq.floor.ui.humanize
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/** The typed values of the registration form; the screen owns them so a success can clear them. */
data class RegistrationFields(val name: String = "", val email: String = "", val phone: String = "")

/** A channel's name in words: the physical store is "In store". */
@Composable
internal fun channelLabel(name: String): String =
    if (name == PHYSICAL_STORE) stringResource(R.string.sale_channel_in_store) else humanize(name)

internal fun isValidEmail(email: String): Boolean =
    email.isBlank() || Patterns.EMAIL_ADDRESS.matcher(email.trim()).matches()

/**
 * The new-customer form in a modal bottom sheet: labeled fields with the right keyboards,
 * inline validation, the registration channel as 48dp radio rows, and one primary action.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RegistrationSheet(
    fields: RegistrationFields,
    onFields: (RegistrationFields) -> Unit,
    registration: RegistrationState,
    onChooseChannel: (Int) -> Unit,
    onLoadChannels: () -> Unit,
    onSubmit: () -> Unit,
    onDismiss: () -> Unit
) {
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ) {
        RegistrationForm(fields, onFields, registration, onChooseChannel, onLoadChannels, onSubmit)
    }
}

@Composable
fun RegistrationForm(
    fields: RegistrationFields,
    onFields: (RegistrationFields) -> Unit,
    registration: RegistrationState,
    onChooseChannel: (Int) -> Unit,
    onLoadChannels: () -> Unit,
    onSubmit: () -> Unit
) {
    val emailValid = isValidEmail(fields.email)
    val canSubmit =
        fields.name.isNotBlank() && emailValid && registration.selectedChannelId != null && !registration.loading
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).imePadding().padding(horizontal = Spacing.Lg)
            .padding(bottom = Spacing.Xxl),
        verticalArrangement = Arrangement.spacedBy(Spacing.Md)
    ) {
        Text(
            stringResource(R.string.sale_register_title),
            style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.semantics { heading() }
        )
        registration.error?.let { MqErrorPanel(it) }
        if (registration.timeoutAdvice) {
            Text(stringResource(R.string.sale_register_unknown), style = MaterialTheme.typography.bodyMedium)
        }
        MqTextField(
            fields.name,
            { onFields(fields.copy(name = it)) },
            stringResource(R.string.sale_field_name),
            supportingText = stringResource(R.string.sale_field_required),
            errorText = fieldError(registration.error, "name"),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Words, imeAction = ImeAction.Next)
        )
        MqTextField(
            fields.email,
            { onFields(fields.copy(email = it)) },
            stringResource(R.string.sale_field_email),
            errorText = if (emailValid) {
                fieldError(
                    registration.error,
                    "email"
                )
            } else {
                stringResource(R.string.sale_email_invalid)
            },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Next)
        )
        MqTextField(
            fields.phone,
            { onFields(fields.copy(phone = it)) },
            stringResource(R.string.sale_field_phone),
            errorText = fieldError(registration.error, "phone"),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { if (canSubmit) onSubmit() })
        )
        Text(
            stringResource(R.string.sale_register_channel),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.semantics { heading() }
        )
        when {
            registration.channels.isEmpty() && registration.loading -> MqLoading(
                stringResource(R.string.sale_channels_loading)
            )
            registration.channels.isEmpty() ->
                MqButton(
                    stringResource(R.string.sale_channels_load),
                    onLoadChannels,
                    variant = MqButtonVariant.Secondary
                )
            else ->
                registration.channels.forEach { channel ->
                    Row(
                        Modifier.fillMaxWidth().heightIn(min = Spacing.X5l).selectable(
                            selected = registration.selectedChannelId == channel.channelId,
                            role = Role.RadioButton,
                            onClick = { onChooseChannel(channel.channelId) }
                        ),
                        horizontalArrangement = Arrangement.spacedBy(Spacing.Sm),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        RadioButton(selected = registration.selectedChannelId == channel.channelId, onClick = null)
                        Text(channelLabel(channel.name), style = MaterialTheme.typography.bodyLarge)
                    }
                }
        }
        fieldError(registration.error, "registration_channel_id")?.let {
            Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
        }
        MqButton(
            stringResource(R.string.sale_register_submit),
            onSubmit,
            enabled = canSubmit || registration.loading,
            loading = registration.loading,
            fullWidth = true
        )
    }
}

/** The service's refusal for one field, in words. */
private fun fieldError(error: ApiError?, field: String): String? =
    error?.details.orEmpty().firstOrNull { it.field == field }?.code?.let(::humanize)
