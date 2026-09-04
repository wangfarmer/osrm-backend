#include "engine/access_point_index.hpp"

#include "util/coordinate_calculation.hpp"
#include "util/log.hpp"

#include <boost/filesystem.hpp>
#include <boost/geometry/index/predicates.hpp>

#include <fstream>
#include <iterator>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

namespace osrm
{
namespace engine
{

namespace
{

constexpr double LONGITUDE_SCALE = 0.7880107536;

std::vector<std::string> SplitCSVLine(const std::string &line)
{
    std::vector<std::string> fields;
    std::stringstream stream(line);
    std::string field;

    while (std::getline(stream, field, ','))
    {
        fields.push_back(field);
    }

    return fields;
}

} // namespace

AccessPointIndex::AccessPointIndex(const std::string &path)
{
    if (path.empty())
    {
        return;
    }

    if (!boost::filesystem::is_regular_file(path))
    {
        util::Log(logWARNING)
            << "Access-point index not found at " << path
            << "; using default snapping";
        return;
    }

    std::ifstream input(path);

    if (!input)
    {
        util::Log(logWARNING)
            << "Could not open access-point index " << path
            << "; using default snapping";
        return;
    }

    std::string line;

    // Skip CSV header.
    std::getline(input, line);

    std::size_t malformed_records = 0;

    while (std::getline(input, line))
    {
        const auto fields = SplitCSVLine(line);

        if (fields.size() != 7)
        {
            ++malformed_records;
            continue;
        }

        try
        {
            const auto way_id = std::stoull(fields[0]);
            const auto approach_lon = std::stod(fields[2]);
            const auto approach_lat = std::stod(fields[3]);
            const auto access_lon = std::stod(fields[5]);
            const auto access_lat = std::stod(fields[6]);

            records.push_back(
                Record{
                    way_id,
                    util::Coordinate{
                        util::FloatLongitude{approach_lon},
                        util::FloatLatitude{approach_lat}},
                    util::Coordinate{
                        util::FloatLongitude{access_lon},
                        util::FloatLatitude{access_lat}}});
        }
        catch (const std::exception &)
        {
            ++malformed_records;
        }
    }

    std::vector<IndexValue> index_values;
    index_values.reserve(records.size());

    for (std::size_t index = 0; index < records.size(); ++index)
    {
        index_values.emplace_back(
            ToIndexPoint(records[index].approach_coordinate),
            index);
    }

    rtree = RTree(index_values.begin(), index_values.end());

    util::Log()
        << "Loaded " << records.size()
        << " access-point records from " << path;

    if (malformed_records > 0)
    {
        util::Log(logWARNING)
            << "Skipped " << malformed_records
            << " malformed access-point records";
    }
}

bool AccessPointIndex::IsLoaded() const
{
    return !records.empty();
}

std::size_t AccessPointIndex::Size() const
{
    return records.size();
}

AccessPointIndex::IndexPoint
AccessPointIndex::ToIndexPoint(const util::Coordinate &coordinate)
{
    const auto longitude =
        static_cast<double>(util::toFloating(coordinate.lon));

    const auto latitude =
        static_cast<double>(util::toFloating(coordinate.lat));

    IndexPoint point;

    boost::geometry::set<0>(
        point,
        longitude * LONGITUDE_SCALE);

    boost::geometry::set<1>(
        point,
        latitude);

    return point;
}

boost::optional<AccessPointMatch>
AccessPointIndex::FindNearest(
    const util::Coordinate &input_coordinate,
    const double maximum_distance) const
{
    if (records.empty())
    {
        return boost::none;
    }

    std::vector<IndexValue> nearest_values;

    rtree.query(
        boost::geometry::index::nearest(
            ToIndexPoint(input_coordinate),
            8),
        std::back_inserter(nearest_values));

    const Record *best_record = nullptr;
    double best_distance = std::numeric_limits<double>::max();

    for (const auto &value : nearest_values)
    {
        const auto &record = records[value.second];

        const auto distance =
            util::coordinate_calculation::greatCircleDistance(
                input_coordinate,
                record.approach_coordinate);

        if (distance < best_distance)
        {
            best_distance = distance;
            best_record = &record;
        }
    }

    if (best_record == nullptr ||
        best_distance > maximum_distance)
    {
        return boost::none;
    }

    return AccessPointMatch{
        best_record->access_coordinate,
        best_distance,
        best_record->way_id};
}

} // namespace engine
} // namespace osrm