-- NRM1 element hierarchy (group elements 0-8, and the level-2 elements
-- this project uses). Names follow the RICS New Rules of Measurement 1.
-- Groups 9+ (preliminaries, overheads, fees, risk) are cost-only and are
-- left out because they carry no material quantities.
-- Safe to run more than once.

INSERT OR IGNORE INTO elements (code, name, parent_id) VALUES
    ('0', 'Facilitating works', NULL),
    ('1', 'Substructure', NULL),
    ('2', 'Superstructure', NULL),
    ('3', 'Internal finishes', NULL),
    ('4', 'Fittings, furnishings and equipment', NULL),
    ('5', 'Services', NULL),
    ('6', 'Prefabricated buildings and building units', NULL),
    ('7', 'Work to existing buildings', NULL),
    ('8', 'External works', NULL);

INSERT OR IGNORE INTO elements (code, name, parent_id)
SELECT c.code, c.name, p.id
FROM (
    SELECT '1.1' AS code, 'Substructure' AS name, '1' AS parent_code
    UNION ALL SELECT '2.1', 'Frame', '2'
    UNION ALL SELECT '2.2', 'Upper floors', '2'
    UNION ALL SELECT '2.3', 'Roof', '2'
    UNION ALL SELECT '2.4', 'Stairs and ramps', '2'
    UNION ALL SELECT '2.5', 'External walls', '2'
    UNION ALL SELECT '2.6', 'Windows and external doors', '2'
    UNION ALL SELECT '2.7', 'Internal walls and partitions', '2'
    UNION ALL SELECT '2.8', 'Internal doors', '2'
    UNION ALL SELECT '3.1', 'Wall finishes', '3'
    UNION ALL SELECT '3.2', 'Floor finishes', '3'
    UNION ALL SELECT '3.3', 'Ceiling finishes', '3'
) AS c
JOIN elements AS p ON p.code = c.parent_code;
