package online.maxthecoder.mosaiq.floor.ui.shell

import androidx.compose.runtime.saveable.Saver

/**
 * Where the signed-in user is (ADR-0058): four root sections and the sub-states
 * that hang off them. Plain data, so it is saved with `rememberSaveable` and the
 * explicit [FloorLocationSaver]; there is no navigation library.
 *
 * Every new sub-state has to say what Back does: [back] is the one place.
 */
sealed interface FloorLocation {
    /** The root section this location belongs to; a root is its own root. */
    val root: Root

    /** One step back: a sub-state returns to its root, a root to Check-in, Check-in to nothing. */
    val back: FloorLocation?
        get() = if (this != root) root else if (this != CheckIn) CheckIn else null

    sealed interface Root : FloorLocation {
        override val root: Root
            get() = this
    }

    data object CheckIn : Root

    data object Scan : Root

    data object Sale : Root

    data object Outbox : Root

    /** Choosing a store, from the top bar's chip or "Change store"; part of Check-in. */
    data object StorePicker : FloorLocation {
        override val root: Root
            get() = CheckIn
    }

    /** A product opened from a recommendation; part of Scan. */
    data class ProductFromRecommendation(val productId: Int) : FloorLocation {
        override val root: Root
            get() = Scan
    }
}

private const val PRODUCT_PREFIX = "product:"

/** Saved as a short string: the name of the location, plus the product id where it has one. */
val FloorLocationSaver: Saver<FloorLocation, String> =
    Saver(
        save = { location ->
            when (location) {
                FloorLocation.CheckIn -> "check_in"
                FloorLocation.Scan -> "scan"
                FloorLocation.Sale -> "sale"
                FloorLocation.Outbox -> "outbox"
                FloorLocation.StorePicker -> "store_picker"
                is FloorLocation.ProductFromRecommendation -> PRODUCT_PREFIX + location.productId
            }
        },
        restore = { saved ->
            when {
                saved == "scan" -> FloorLocation.Scan
                saved == "sale" -> FloorLocation.Sale
                saved == "outbox" -> FloorLocation.Outbox
                saved == "store_picker" -> FloorLocation.StorePicker
                saved.startsWith(PRODUCT_PREFIX) ->
                    saved.removePrefix(PRODUCT_PREFIX).toIntOrNull()?.let {
                        FloorLocation.ProductFromRecommendation(it)
                    } ?: FloorLocation.Scan
                // "check_in", and anything unknown, lands on the start.
                else -> FloorLocation.CheckIn
            }
        }
    )
