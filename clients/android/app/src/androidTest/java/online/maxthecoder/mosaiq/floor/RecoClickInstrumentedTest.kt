package online.maxthecoder.mosaiq.floor

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class RecoClickInstrumentedTest : RecoClickScenario() {
    override fun fixture(path: String): String =
        InstrumentationRegistry.getInstrumentation().context.assets.open(path).bufferedReader().use { it.readText() }
}
