package online.maxthecoder.mosaiq.floor

import android.content.Context
import androidx.annotation.PluralsRes
import androidx.annotation.StringRes
import java.util.ServiceLoader

/**
 * Words for code that has no composition to ask: view models, the network layer and the write queue.
 * Composables use `stringResource`; everything else goes through here so no sentence lives in code.
 */
object AppText {
    /** Looks a resource up; the app installs one backed by [Context], tests may provide their own. */
    interface Resolver {
        fun string(@StringRes id: Int, args: Array<out Any>): String

        fun plural(@PluralsRes id: Int, quantity: Int, args: Array<out Any>): String
    }

    @Volatile private var installed: Resolver? = null

    /** Called once from the application; resources follow the process's locale. */
    fun install(context: Context) {
        val resources = context.applicationContext.resources
        installed =
            object : Resolver {
                override fun string(id: Int, args: Array<out Any>) = resources.getString(id, *args)

                override fun plural(id: Int, quantity: Int, args: Array<out Any>) =
                    resources.getQuantityString(id, quantity, *args)
            }
    }

    private val resolver: Resolver
        get() =
            installed
                ?: ServiceLoader.load(Resolver::class.java).firstOrNull()
                ?: error("AppText is not installed")

    fun get(@StringRes id: Int, vararg args: Any): String = resolver.string(id, args)

    fun plural(@PluralsRes id: Int, quantity: Int, vararg args: Any): String = resolver.plural(id, quantity, args)
}
