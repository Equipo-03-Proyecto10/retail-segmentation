package online.maxthecoder.mosaiq.floor.ui.shell

import androidx.compose.runtime.saveable.Saver
import online.maxthecoder.mosaiq.floor.auth.FloorAccess
import online.maxthecoder.mosaiq.floor.auth.FloorSection

/**
 * Where the signed-in user is (ADR-0058): the root sections and the sub-states
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

    data object Customer : Root

    /** Choosing a store, from the top bar's chip or "Change store"; part of Check-in. */
    data object StorePicker : FloorLocation {
        override val root: Root
            get() = CheckIn
    }

    /** Registering a customer in the sale flow; Back closes the sheet and returns to Sale. */
    data object RegisterCustomer : FloorLocation {
        override val root: Root
            get() = Sale
    }

    /** A customer identified by card or search, by id only: never the scanned code. Part of Customer. */
    data class CustomerProfile(val customerId: String) : FloorLocation {
        override val root: Root
            get() = Customer
    }

    /** A product opened from a recommendation; part of Scan. */
    data class ProductFromRecommendation(val productId: Int) : FloorLocation {
        override val root: Root
            get() = Scan
    }
}

/** The section this location is in, which is what a role's profile is made of. */
val FloorLocation.section: FloorSection
    get() =
        when (root) {
            FloorLocation.CheckIn -> FloorSection.CHECK_IN
            FloorLocation.Scan -> FloorSection.SCAN
            FloorLocation.Sale -> FloorSection.SALE
            FloorLocation.Outbox -> FloorSection.OUTBOX
            FloorLocation.Customer -> FloorSection.CUSTOMER
        }

val FloorSection.root: FloorLocation.Root
    get() =
        when (this) {
            FloorSection.CHECK_IN -> FloorLocation.CheckIn
            FloorSection.SCAN -> FloorLocation.Scan
            FloorSection.SALE -> FloorLocation.Sale
            FloorSection.OUTBOX -> FloorLocation.Outbox
            FloorSection.CUSTOMER -> FloorLocation.Customer
        }

/**
 * This location when [role] holds its section, otherwise where the role opens. Every way
 * into a location (a tap, Back, a state restored after process death) goes through here,
 * so a section a role does not hold cannot be shown by any of them. A role that holds
 * nothing lands on Check-in, which the shell never draws for it.
 */
fun FloorLocation.allowedFor(role: String?): FloorLocation =
    if (FloorAccess.holds(role, section)) this else (FloorAccess.start(role)?.root ?: FloorLocation.CheckIn)

/** [FloorLocation.back], kept inside what [role] holds; `null` when it would not move. */
fun FloorLocation.backFor(role: String?): FloorLocation? = back?.allowedFor(role)?.takeIf { it != this }

private const val PRODUCT_PREFIX = "product:"
private const val CUSTOMER_PREFIX = "customer:"

/** Saved as a short string: the name of the location, plus the product or customer id where it has one. */
val FloorLocationSaver: Saver<FloorLocation, String> =
    Saver(
        save = { location ->
            when (location) {
                FloorLocation.CheckIn -> "check_in"
                FloorLocation.Scan -> "scan"
                FloorLocation.Sale -> "sale"
                FloorLocation.Outbox -> "outbox"
                FloorLocation.Customer -> "customer"
                FloorLocation.StorePicker -> "store_picker"
                FloorLocation.RegisterCustomer -> "register_customer"
                is FloorLocation.ProductFromRecommendation -> PRODUCT_PREFIX + location.productId
                is FloorLocation.CustomerProfile -> CUSTOMER_PREFIX + location.customerId
            }
        },
        restore = { saved ->
            when {
                saved == "scan" -> FloorLocation.Scan
                saved == "sale" -> FloorLocation.Sale
                saved == "outbox" -> FloorLocation.Outbox
                saved == "customer" -> FloorLocation.Customer
                saved == "store_picker" -> FloorLocation.StorePicker
                saved == "register_customer" -> FloorLocation.RegisterCustomer
                saved.startsWith(PRODUCT_PREFIX) ->
                    saved.removePrefix(PRODUCT_PREFIX).toIntOrNull()?.let {
                        FloorLocation.ProductFromRecommendation(it)
                    } ?: FloorLocation.Scan
                saved.startsWith(CUSTOMER_PREFIX) ->
                    saved.removePrefix(CUSTOMER_PREFIX).takeIf { it.isNotBlank() }?.let {
                        FloorLocation.CustomerProfile(it)
                    } ?: FloorLocation.Customer
                // "check_in", and anything unknown, lands on the start.
                else -> FloorLocation.CheckIn
            }
        }
    )
