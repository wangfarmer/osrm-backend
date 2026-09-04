#ifndef OSRM_ENGINE_ACCESS_POINT_INDEX_HPP
#define OSRM_ENGINE_ACCESS_POINT_INDEX_HPP

#include "util/coordinate.hpp"

#include <boost/geometry.hpp>
#include <boost/geometry/index/rtree.hpp>
#include <boost/optional.hpp>
#include "engine/access_point_index.hpp"

#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace osrm
{
namespace engine
{

struct AccessPointMatch
{
    util::Coordinate access_coordinate;
    double approach_distance;
    std::uint64_t source_way_id;
};

class AccessPointIndex
{
  private:
    using IndexPoint =
        boost::geometry::model::point<double, 2, boost::geometry::cs::cartesian>;

    using IndexValue = std::pair<IndexPoint, std::size_t>;

    using RTree = boost::geometry::index::rtree<
        IndexValue,
        boost::geometry::index::rstar<16>>;

    struct Record
    {
        std::uint64_t way_id;
        util::Coordinate approach_coordinate;
        util::Coordinate access_coordinate;
    };

  public:
    explicit AccessPointIndex(const std::string &path);

    bool IsLoaded() const;
    std::size_t Size() const;

    boost::optional<AccessPointMatch>
    FindNearest(const util::Coordinate &input_coordinate,
                double maximum_distance) const;

  private:
    static IndexPoint ToIndexPoint(const util::Coordinate &coordinate);

    std::vector<Record> records;
    RTree rtree;
};

} // namespace engine
} // namespace osrm

#endif