-- Dragon Mail phase 2-4 (T-0049): comments under drawings, and timed IP bans.
CREATE TABLE comments (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    drawing_id  uuid        NOT NULL REFERENCES drawings (id) ON DELETE CASCADE,
    body        text        NOT NULL CHECK (length(body) BETWEEN 1 AND 2000),
    created_at  timestamptz NOT NULL DEFAULT now(),
    ip          inet        NOT NULL,
    hidden      boolean     NOT NULL DEFAULT false
);
CREATE INDEX comments_drawing_idx ON comments (drawing_id, created_at);
CREATE INDEX comments_ip_idx ON comments (ip);

-- A ban is a timeout: it always ends. `network` holds a single address (/32, /128)
-- or a whole range. `scope` says what it blocks.
CREATE TABLE bans (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    network     cidr        NOT NULL,
    scope       text        NOT NULL DEFAULT 'all' CHECK (scope IN ('all', 'draw', 'comment')),
    reason      text        NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    lifted_at   timestamptz,
    CHECK (expires_at > created_at)
);
CREATE INDEX bans_network_idx ON bans USING gist (network inet_ops);

-- Drawings remember their size, so the gallery can show an odd-sized one at its real
-- size in the frame (NULL = the standard 500x500 canvas). Drawings imported from before
-- the database have no IP.
ALTER TABLE drawings
    ADD COLUMN width  integer CHECK (width > 0),
    ADD COLUMN height integer CHECK (height > 0),
    ALTER COLUMN ip DROP NOT NULL;
