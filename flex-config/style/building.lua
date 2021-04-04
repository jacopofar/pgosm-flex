require "helpers"

local tables = {}


tables.building_point = osm2pgsql.define_table({
    name = 'building_point',
    schema = schema_name,
    ids = { type = 'node', id_column = 'osm_id' },
    columns = {
        { column = 'osm_type',     type = 'text' , not_null = true},
        { column = 'osm_subtype',   type = 'text'},
        { column = 'name',     type = 'text' },
        { column = 'levels',  type = 'int'},
        { column = 'height',  type = 'numeric'},
        { column = 'housenumber', type = 'text'},
        { column = 'street',     type = 'text' },
        { column = 'city',     type = 'text' },
        { column = 'state', type = 'text'},
        { column = 'address', type = 'text', not_null = true},
        { column = 'wheelchair', type = 'bool'},
        { column = 'operator', type = 'text'},
        { column = 'geom',     type = 'point', projection = srid},
    }
})


tables.building_polygon = osm2pgsql.define_table({
    name = 'building_polygon',
    schema = schema_name,
    ids = { type = 'way', id_column = 'osm_id' },
    columns = {
        { column = 'osm_type',     type = 'text' , not_null = true},
        { column = 'osm_subtype',   type = 'text'},
        { column = 'name',     type = 'text' },
        { column = 'levels',  type = 'int'},
        { column = 'height',  type = 'numeric'},
        { column = 'housenumber', type = 'text'},
        { column = 'street',     type = 'text' },
        { column = 'city',     type = 'text' },
        { column = 'state', type = 'text'},
        { column = 'address', type = 'text', not_null = true},
        { column = 'wheelchair', type = 'bool'},
        { column = 'operator', type = 'text'},
        { column = 'geom',     type = 'multipolygon', projection = srid},
    }
})


function address_only_building(tags)
    -- Cannot have any of these tags
    if tags.shop
        or tags.amenity
        or tags.building
        or tags['building:part']
        or tags.landuse
        or tags.leisure
        or tags.office
        or tags.tourism then
            return false
    end

    -- Opting to include any addr: tag that was not excluded explicitly above
    --   This might be too wide of a net, but trying to be too picky risks
    --   excluding potentially important data
    for k, v in pairs(tags) do
        if k ~= nil then
            if starts_with(k, "addr:") then
                return true
            end
        end
    end
    return false
end


function building_process_node(object)
    local address_only = address_only_building(object.tags)

    if not object.tags.building
            and not object.tags['building:part']
            and not object.tags.office
            and not address_only
            then
        return
    end

    local osm_type
    local osm_subtype

    if object.tags.building then
        osm_type = 'building'
        osm_subtype = object.tags.building
    elseif object.tags['building:part'] then
        osm_type = 'building_part'
        osm_subtype = object.tags['building:part']
    elseif object.tags.office then
        osm_type = 'office'
        osm_subtype = object.tags.office
    elseif address_only then
        osm_type = 'address'
        osm_subtype = nil
    else
        osm_type = 'unknown'
        osm_subtype = nil
    end

    local name = get_name(object.tags)
    local housenumber  = object.tags['addr:housenumber']
    local street = object.tags['addr:street']
    local city = object.tags['addr:city']
    local state = object.tags['addr:state']
    local address = get_address(object.tags)
    local wheelchair = object:grab_tag('wheelchair')
    local levels = object:grab_tag('building:levels')
    local height = parse_to_meters(object.tags['height'])
    local operator  = object:grab_tag('operator')

    tables.building_point:add_row({
        osm_type = osm_type,
        osm_subtype = osm_subtype,
        name = name,
        housenumber = housenumber,
        street = street,
        city = city,
        state = state,
        address = address,
        wheelchair = wheelchair,
        levels = levels,
        height = height,
        operator = operator,
        geom = { create = 'point' }
    })


end


function building_process_way(object)
    local address_only = address_only_building(object.tags)

    if not object.tags.building
            and not object.tags['building:part']
            and not address_only
            and not object.tags.office
                then
        return
    end

    if not object.is_closed then
        return
    end

    if object.tags.building then
        osm_type = 'building'
        osm_subtype = object.tags.building
    elseif object.tags['building:part'] then
        osm_type = 'building_part'
        osm_subtype = object.tags['building:part']
    elseif object.tags.office then
        osm_type = 'office'
        osm_subtype = object.tags.office
    elseif address_only then
        osm_type = 'address'
        osm_subtype = nil
    else
        osm_type = 'unknown'
        osm_subtype = nil
    end

    local name = get_name(object.tags)
    local housenumber  = object.tags['addr:housenumber']
    local street = object.tags['addr:street']
    local city = object.tags['addr:city']
    local state = object.tags['addr:state']
    local address = get_address(object.tags)
    local wheelchair = object:grab_tag('wheelchair')
    local levels = object:grab_tag('building:levels')
    local height = parse_to_meters(object.tags['height'])
    local operator  = object:grab_tag('operator')

    tables.building_polygon:add_row({
        osm_type = osm_type,
        osm_subtype = osm_subtype,
        name = name,
        housenumber = housenumber,
        street = street,
        city = city,
        state = state,
        address = address,
        wheelchair = wheelchair,
        levels = levels,
        height = height,
        operator = operator,
        geom = { create = 'area' }
    })


end



if osm2pgsql.process_way == nil then
    osm2pgsql.process_way = building_process_way
else
    local nested = osm2pgsql.process_way
    osm2pgsql.process_way = function(object)
        local object_copy = deep_copy(object)
        nested(object)
        building_process_way(object_copy)
    end
end


if osm2pgsql.process_node == nil then
    osm2pgsql.process_node = building_process_node
else
    local nested = osm2pgsql.process_node
    osm2pgsql.process_node = function(object)
        local object_copy = deep_copy(object)
        nested(object)
        building_process_node(object_copy)
    end
end
