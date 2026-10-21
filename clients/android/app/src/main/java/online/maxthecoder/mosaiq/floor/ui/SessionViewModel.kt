package online.maxthecoder.mosaiq.floor.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import online.maxthecoder.mosaiq.floor.auth.SessionEvents
import online.maxthecoder.mosaiq.floor.auth.SessionRepository
import online.maxthecoder.mosaiq.floor.auth.SessionResult
import online.maxthecoder.mosaiq.floor.net.ApiError

/** What the screen shows. */
sealed interface SessionState {
    /** The sign-in form, with why the user is there when it was not their choice. */
    data class SignIn(
        val notice: String? = null,
        val error: ApiError? = null
    ) : SessionState

    data object Loading : SessionState

    data class SignedIn(
        val name: String,
        val role: String
    ) : SessionState

    /** Signed in, but the profile could not be read; the tokens are kept. */
    data class Failed(
        val error: ApiError
    ) : SessionState
}

class SessionViewModel(
    private val repository: SessionRepository,
    events: SessionEvents
) : ViewModel() {
    private val mutableState =
        MutableStateFlow<SessionState>(if (repository.isSignedIn) SessionState.Loading else SessionState.SignIn())
    val state: StateFlow<SessionState> = mutableState

    init {
        viewModelScope.launch {
            events.signedOut.collect { mutableState.value = SessionState.SignIn(notice = it.reason) }
        }
        if (repository.isSignedIn) refreshProfile()
    }

    fun signIn(email: String, password: String) {
        mutableState.value = SessionState.Loading
        viewModelScope.launch { mutableState.value = show(repository.signIn(email, password)) }
    }

    fun refreshProfile() {
        mutableState.value = SessionState.Loading
        viewModelScope.launch { mutableState.value = show(repository.currentUser()) }
    }

    fun signOut() {
        viewModelScope.launch {
            repository.signOut()
            mutableState.value = SessionState.SignIn()
        }
    }

    private fun show(result: SessionResult): SessionState = when (result) {
        is SessionResult.SignedIn -> SessionState.SignedIn(result.user.name, result.user.roleCode)
        is SessionResult.RoleRefused ->
            SessionState.SignIn(notice = "This app is for store managers and administrators. You were signed out.")
        is SessionResult.Failed ->
            if (repository.isSignedIn) SessionState.Failed(result.error) else SessionState.SignIn(error = result.error)
    }
}
