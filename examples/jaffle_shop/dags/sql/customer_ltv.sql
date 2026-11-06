create schema if not exists reporting;

create or replace table reporting.customer_ltv as
select
    c.customer_id,
    c.first_name,
    c.customer_lifetime_value as lifetime_value,
    c.number_of_orders,
    case when c.number_of_orders >= 3 then 'loyal' else 'new' end as segment
from main.customers as c
where c.customer_lifetime_value > 0;
