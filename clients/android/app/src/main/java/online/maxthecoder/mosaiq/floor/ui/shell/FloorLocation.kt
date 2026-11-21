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

    /** Registering a customer with their consent (F27-09); part of Customer, in the customer record's section. */
    data object NewCustomer : FloorLocation {
        override val root: Root
            get() = Customer
    }

    /**
     * A customer's record: consent, benefits, preferences and their history (F27-09), by id only.
     * Back returns to that customer's profile, which is where it was opened from.
     */
    data class CustomerRecord(val customerId: String) : FloorLocation {
        override val root: Root
            get() = Customer

        override val back: FloorLocation
            get() = CustomerProfile(customerId)
    }

    /**
     * The updates screen (ADR-0071), opened from the top bar or a notification. [focus] is what a notification
     * pointed at (`campaign:<id>` or `incident:<id>`). It hangs off the Outbox, which every role holds, so
     * Back returns there.
     */
    data class Inbox(val focus: String? = null) : FloorLocation {
        override val root: Root
            get() = Outbox
    }

    /** A product opened from a recommendation; part of Scan. */
    data class ProductFromRecommendation(val productId: Int) : FloorLocation {
        override val root: Root
            get() = Scan
    }
}

/**
 * The section this location is in, which is what a role's profile is made of. The customer record's
 * locations hang off Customer but are their own section, so holding Customer does not open them.
 */
val FloorLocation.section: FloorSection
    get() =
        when (this) {
            FloorLocation.NewCustomer, is FloorLocation.CustomerRecord -> FloorSection.CUSTOMER_RECORD
            else ->
                when (root) {
                    FloorLocation.CheckIn -> FloorSection.CHECK_IN
                    FloorLocation.Scan -> FloorSection.SCAN
                    FloorLocation.Sale -> FloorSection.SALE
                    FloorLocation.Outbox -> FloorSection.OUTBOX
                    FloorLocation.Customer -> FloorSection.CUSTOMER
                }
        }

val FloorSection.root: FloorLocation.Root
    get() =
        when (this) {
            FloorSection.CHECK_IN -> FloorLocation.CheckIn
            FloorSection.SCAN -> FloorLocation.Scan
            FloorSection.SALE -> FloorLocation.Sale
            FloorSection.OUTBOX -> FloorLocation.Outbox
            FloorSection.CUSTOMER -> FloorLocation.Customer
            // No bar entry of its own: it is reached from Customer.
            FloorSection.CUSTOMER_RECORD -> FloorLocation.Customer
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
private const val RECORD_PREFIX = "record:"
private const val INBOX = "inbox"
private const val INBOX_PREFIX = "inbox:"

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
                FloorLocation.NewCustomer -> "new_customer"
                is FloorLocation.CustomerRecord -> RECORD_PREFIX + location.customerId
                is FloorLocation.Inbox -> location.focus?.let { INBOX_PREFIX + it } ?: INBOX
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
                saved == INBOX -> FloorLocation.Inbox()
                saved.startsWith(INBOX_PREFIX) -> FloorLocation.Inbox(saved.removePrefix(INBOX_PREFIX))
                saved.startsWith(PRODUCT_PREFIX) ->
                    saved.removePrefix(PRODUCT_PREFIX).toIntOrNull()?.let {
                        FloorLocation.ProductFromRecommendation(it)
                    } ?: FloorLocation.Scan
                saved.startsWith(CUSTOMER_PREFIX) ->
                    saved.removePrefix(CUSTOMER_PREFIX).takeIf { it.isNotBlank() }?.let {
                        FloorLocation.CustomerProfile(it)
                    } ?: FloorLocation.Customer
                saved == "new_customer" -> FloorLocation.NewCustomer
                saved.startsWith(RECORD_PREFIX) ->
                    saved.removePrefix(RECORD_PREFIX).takeIf { it.isNotBlank() }?.let {
                        FloorLocation.CustomerRecord(it)
                    } ?: FloorLocation.Customer
                // "check_in", and anything unknown, lands on the start.
                else -> FloorLocation.CheckIn
            }
        }
    )
