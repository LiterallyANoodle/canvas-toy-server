-- Dragon Mail drawings (T-0049 phase 1).
-- `number` is the public gallery number: an identity column, so two concurrent
-- submissions can't get the same one (the original computed max+1 in two steps).
CREATE TABLE drawings (
    id          uuid        PRIMARY KEY,
    number      bigint      GENERATED ALWAYS AS IDENTITY UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    ip          inet        NOT NULL,
    hidden      boolean     NOT NULL DEFAULT false
);
CREATE INDEX drawings_ip_idx ON drawings (ip);
