-- ADR-0069 (F25-12): what a loaded database must satisfy about sale attribution.
-- Every statement selects violations, so a database that holds prints nothing:
--   psql -X -At -v ON_ERROR_STOP=1 -d retail -f contracts/db/check_attribution.sql
-- CI runs it on the seeded database, and the experimentation tests run it on the
-- databases they build. It reads as the owner: the attribution table is in the
-- experiments schema, which the web cannot name (ADR-0046). The last statement is
-- judged over the exposures that exist when it runs.

-- For any period, the attributed net revenue of all campaigns together never exceeds
-- the period's net sales (the net_sale view, ADR-0062). A period is a calendar month
-- of the sale. Net sales are summed on their own, before the join, so a sale held
-- twice would show up on the attributed side only.
SELECT 'ADR-0069 attributed revenue exceeds net sales', held.period,
       held.attributed, sold.net_sales
  FROM (SELECT date_trunc('month', s.occurred_at) AS period,
               sum(s.net_total) AS attributed
          FROM experiments.sale_attribution AS a
          JOIN public.net_sale AS s ON s.transaction_id = a.sale_id
         GROUP BY 1) AS held
  JOIN (SELECT date_trunc('month', s.occurred_at) AS period,
               sum(s.net_total) AS net_sales
          FROM public.net_sale AS s
         GROUP BY 1) AS sold USING (period)
 WHERE held.attributed > sold.net_sales;

-- One holder per sale.
SELECT 'ADR-0069 sale held more than once', sale_id, count(*)
  FROM experiments.sale_attribution
 GROUP BY sale_id
HAVING count(*) > 1;

-- The holder is a treatment assignment of an experiment with a campaign, of the sale's
-- own customer, with an exposure of that assignment before the sale, and the sale is
-- inside the window.
SELECT 'ADR-0069 holder is not an eligible treatment assignment', a.attribution_id,
       a.sale_id, a.assignment_id
  FROM experiments.sale_attribution AS a
  JOIN public.experiment_assignment AS h ON h.assignment_id = a.assignment_id
  JOIN public.experiment AS e ON e.experiment_id = h.experiment_id
  JOIN public.experiment_group AS g
    ON g.group_id = h.group_id AND g.experiment_id = h.experiment_id
  JOIN public.experiment_exposure AS x ON x.exposure_id = a.exposure_id
  JOIN public.transaction AS t ON t.transaction_id = a.sale_id
 WHERE g.kind <> 'TREATMENT'
    OR e.campaign_id IS NULL
    OR t.customer_id <> h.customer_id
    OR x.assignment_id <> a.assignment_id
    OR x.exposed_at >= t.occurred_at
    OR t.occurred_at < h.assigned_at
    OR t.occurred_at >= h.assigned_at + make_interval(hours => 24 * e.conversion_window_days)
    OR a.attributed_at < h.assigned_at + make_interval(hours => 24 * e.conversion_window_days);

-- The holder is the campaign whose exposure was the latest before the sale, the lower
-- assignment breaking a tie: no other treatment assignment of the customer, of an
-- experiment with a campaign and with the sale in its window, has a later exposure
-- (a voided exposure never counts).
SELECT 'ADR-0069 holder is not the latest touch', a.sale_id, a.assignment_id,
       other.assignment_id
  FROM experiments.sale_attribution AS a
  JOIN public.experiment_assignment AS h ON h.assignment_id = a.assignment_id
  JOIN public.experiment_exposure AS hx ON hx.exposure_id = a.exposure_id
  JOIN public.transaction AS t ON t.transaction_id = a.sale_id
  JOIN public.experiment_assignment AS other
    ON other.customer_id = h.customer_id AND other.assignment_id <> h.assignment_id
  JOIN public.experiment AS oe
    ON oe.experiment_id = other.experiment_id AND oe.campaign_id IS NOT NULL
  JOIN public.experiment_group AS og
    ON og.group_id = other.group_id AND og.experiment_id = other.experiment_id
   AND og.kind = 'TREATMENT'
  JOIN public.experiment_exposure AS ox ON ox.assignment_id = other.assignment_id
 WHERE t.occurred_at >= other.assigned_at
   AND t.occurred_at < other.assigned_at + make_interval(hours => 24 * oe.conversion_window_days)
   AND ox.exposed_at < t.occurred_at
   AND NOT EXISTS (SELECT FROM experiments.exposure_notice AS v
                    WHERE v.source_event_id = ox.source_event_id AND v.notice = 'VOIDED')
   AND (ox.exposed_at > hx.exposed_at
        OR (ox.exposed_at = hx.exposed_at AND other.assignment_id < h.assignment_id));
