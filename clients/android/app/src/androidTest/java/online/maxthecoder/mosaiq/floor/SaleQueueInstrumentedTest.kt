package online.maxthecoder.mosaiq.floor

import android.app.Application
import android.content.Context
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.AndroidJUnitRunner
import org.junit.runner.RunWith

class SalesTestRunner : AndroidJUnitRunner() {
    override fun newApplication(cl: ClassLoader, className: String, context: Context): Application =
        super.newApplication(cl, SalesTestApplication::class.java.name, context)
}

@RunWith(AndroidJUnit4::class)
class SaleQueueInstrumentedTest : SaleQueueScenario() {
    override fun fixture(path: String): String =
        InstrumentationRegistry.getInstrumentation().context.assets.open(path).bufferedReader().use { it.readText() }
}
