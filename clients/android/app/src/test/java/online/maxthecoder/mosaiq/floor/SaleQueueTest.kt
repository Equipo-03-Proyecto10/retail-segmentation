package online.maxthecoder.mosaiq.floor

import androidx.test.ext.junit.runners.AndroidJUnit4
import java.io.File
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

@RunWith(AndroidJUnit4::class)
@Config(sdk = [35], application = SalesTestApplication::class)
class SaleQueueTest : SaleQueueScenario() {
    override fun fixture(path: String): String = File("../../../contracts", path).readText()
}
