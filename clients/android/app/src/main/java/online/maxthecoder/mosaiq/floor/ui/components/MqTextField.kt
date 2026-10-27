package online.maxthecoder.mosaiq.floor.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.error
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import online.maxthecoder.mosaiq.floor.R
import online.maxthecoder.mosaiq.floor.ui.theme.Spacing

/**
 * The MOSAIQ text field: the label is always visible (never a placeholder alone),
 * the border uses the theme outline for 3:1 contrast, and an [errorText] shows in
 * the error color with an alert icon, so color is never the only signal.
 *
 * With [password] the text is hidden and a toggle shows it; the choice survives
 * rotation, the text itself is the caller's state.
 */
@Composable
fun MqTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    supportingText: String? = null,
    errorText: String? = null,
    enabled: Boolean = true,
    readOnly: Boolean = false,
    singleLine: Boolean = true,
    password: Boolean = false,
    keyboardOptions: KeyboardOptions = KeyboardOptions.Default,
    keyboardActions: KeyboardActions = KeyboardActions.Default,
    leading: (@Composable () -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null
) {
    var visible by rememberSaveable { mutableStateOf(false) }
    val scheme = MaterialTheme.colorScheme
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = modifier.fillMaxWidth().heightIn(min = ControlHeight).semantics {
            if (errorText != null) error(errorText)
        },
        enabled = enabled,
        readOnly = readOnly,
        label = { Text(label) },
        leadingIcon = leading,
        trailingIcon =
        when {
            password -> {
                {
                    IconButton(onClick = { visible = !visible }, modifier = Modifier.size(ControlHeight)) {
                        MqIcon(
                            if (visible) R.drawable.ic_lucide_eye_off else R.drawable.ic_lucide_eye,
                            contentDescription = stringResource(
                                if (visible) R.string.component_hide_password else R.string.component_show_password
                            )
                        )
                    }
                }
            }
            else -> trailing
        },
        supportingText =
        when {
            errorText != null -> {
                {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(Spacing.Xs),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        MqIcon(
                            R.drawable.ic_lucide_circle_alert,
                            contentDescription = null,
                            tint = scheme.error,
                            size = Spacing.Lg
                        )
                        Text(errorText)
                    }
                }
            }
            supportingText != null -> {
                { Text(supportingText) }
            }
            else -> null
        },
        isError = errorText != null,
        visualTransformation =
        if (password && !visible) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = keyboardOptions,
        keyboardActions = keyboardActions,
        singleLine = singleLine,
        shape = MaterialTheme.shapes.small,
        colors =
        OutlinedTextFieldDefaults.colors(
            unfocusedBorderColor = scheme.outline,
            focusedBorderColor = scheme.primary,
            disabledBorderColor = scheme.outlineVariant,
            errorBorderColor = scheme.error
        )
    )
}
