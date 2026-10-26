package online.maxthecoder.mosaiq.floor

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.height
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import online.maxthecoder.mosaiq.floor.ui.FloorApp
import online.maxthecoder.mosaiq.floor.ui.SessionState
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/** The sign-in form on a screen shorter than itself (landscape, small phone, keyboard open): UX-03. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = FloorTestApp::class)
class SignInScrollTest {
    @get:Rule val compose = createComposeRule()

    @Test
    fun theSignInButtonIsReachableByScrollingWhenTheScreenIsShort() {
        compose.setContent {
            Box(Modifier.height(200.dp)) {
                FloorApp(
                    state = SessionState.SignIn(),
                    onSignIn = { _, _ -> },
                    onRetry = {},
                    onSignOut = {}
                )
            }
        }
        compose.onNodeWithText("Sign in").performScrollTo().assertIsDisplayed()
    }
}
