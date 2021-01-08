-- Deploy pgosm-flex:001 to pg

BEGIN;

CREATE SCHEMA pgosm;

CREATE TABLE pgosm.road
(
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    region TEXT NOT NULL DEFAULT 'United States',
    osm_type TEXT NOT NULL,
    route_motor BOOLEAN DEFAULT True,
    route_foot BOOLEAN DEFAULT True,
    route_cycle BOOLEAN DEFAULT True,
    maxspeed NUMERIC(6,2) NOT NULL,
    maxspeed_mph NUMERIC(6,2) NOT NULL
        GENERATED ALWAYS AS (maxspeed / 1.609344
            ) STORED,
    CONSTRAINT uq_pgosm_routable_code UNIQUE (region, osm_type)
);


COMMENT ON TABLE pgosm.road IS 'Provides lookup information for road layers, generally related to routing use cases.';
COMMENT ON COLUMN pgosm.road.region IS 'Allows defining different definitions based on region.  Can be custom defined.';
COMMENT ON COLUMN pgosm.road.osm_type IS 'Value from highway tags.';
COMMENT ON COLUMN pgosm.road.route_motor IS 'Used to filter for classifications that typically allow motorized traffic.';
COMMENT ON COLUMN pgosm.road.route_foot IS 'Used to filter for classifications that typically allow foot traffic.';
COMMENT ON COLUMN pgosm.road.route_cycle IS 'Used to filter for classifications that typically allow bicycle traffic.';
COMMENT ON COLUMN pgosm.road.maxspeed IS 'Maxspeed in km/hr';
COMMENT ON COLUMN pgosm.road.maxspeed_mph IS 'Maxspeed in mph';

COMMIT;
