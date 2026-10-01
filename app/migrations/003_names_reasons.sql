-- T-0049 polish: an optional name on comments, and moderation reasons.
ALTER TABLE comments ADD COLUMN name text CHECK (name IS NULL OR length(name) BETWEEN 1 AND 40);
ALTER TABLE comments ADD COLUMN mod_reason text NOT NULL DEFAULT '';
ALTER TABLE drawings ADD COLUMN mod_reason text NOT NULL DEFAULT '';

-- Every admin action, with its reason; a deleted item's reason lives on here.
CREATE TABLE mod_log (
    id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at      timestamptz NOT NULL DEFAULT now(),
    admin   text        NOT NULL,
    action  text        NOT NULL,
    target  text        NOT NULL,
    reason  text        NOT NULL DEFAULT ''
);

-- What a ban was for (a copy, so it outlives the comment or drawing), and when the
-- banned visitor was last told about it. Only that visitor ever sees the reason.
ALTER TABLE bans
    ADD COLUMN subject_kind text NOT NULL DEFAULT '' CHECK (subject_kind IN ('', 'comment', 'drawing')),
    ADD COLUMN subject_text text NOT NULL DEFAULT '',
    ADD COLUMN subject_at   timestamptz,
    ADD COLUMN notified_at  timestamptz;
