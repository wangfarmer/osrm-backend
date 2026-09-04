#ifndef BASE_PLUGIN_HPP
#define BASE_PLUGIN_HPP

#include "engine/api/base_parameters.hpp"
#include "engine/api/base_result.hpp"
#include "engine/api/flatbuffers/fbresult_generated.h"
#include "engine/datafacade/datafacade_base.hpp"
#include "engine/phantom_node.hpp"
#include "engine/routing_algorithms.hpp"
#include "engine/status.hpp"

#include "util/coordinate.hpp"
#include "util/coordinate_calculation.hpp"
#include "util/integer_range.hpp"
#include "util/json_container.hpp"

#include <algorithm>
#include <cctype>
#include <iterator>
#include <string>
#include <vector>

#include <util/log.hpp>

namespace osrm
{
namespace engine
{
namespace plugins
{

class BasePlugin
{
  protected:
    bool CheckAllCoordinates(const std::vector<util::Coordinate> &coordinates) const
    {
        return !std::any_of(
            std::begin(coordinates), std::end(coordinates), [](const util::Coordinate coordinate) {
                return !coordinate.IsValid();
            });
    }

    bool CheckAlgorithms(const api::BaseParameters &params,
                         const RoutingAlgorithmsInterface &algorithms,
                         osrm::engine::api::ResultT &result) const
    {
        if (algorithms.IsValid())
        {
            return true;
        }

        if (!algorithms.HasExcludeFlags() && !params.exclude.empty())
        {
            Error("NotImplemented", "This algorithm does not support exclude flags.", result);
            return false;
        }
        if (algorithms.HasExcludeFlags() && !params.exclude.empty())
        {
            Error("InvalidValue", "Exclude flag combination is not supported.", result);
            return false;
        }

        BOOST_ASSERT_MSG(false,
                         "There are only two reasons why the algorithm interface can be invalid.");
        return false;
    }

    struct ErrorRenderer
    {
        std::string code;
        std::string message;

        ErrorRenderer(std::string code, std::string message)
            : code(std::move(code)), message(std::move(message)){};

        void operator()(util::json::Object &json_result)
        {
            json_result.values["code"] = code;
            json_result.values["message"] = message;
        };
        void operator()(flatbuffers::FlatBufferBuilder &fb_result)
        {
            auto error = api::fbresult::CreateErrorDirect(fb_result, code.c_str(), message.c_str());
            api::fbresult::FBResultBuilder response(fb_result);
            response.add_error(true);
            response.add_code(error);
            fb_result.Finish(response.Finish());
        };
        void operator()(std::string &str_result)
        {
            str_result = str(boost::format("code=%1% message=%2%") % code % message);
        };
    };

    Status Error(const std::string &code,
                 const std::string &message,
                 osrm::engine::api::ResultT &result) const
    {
        mapbox::util::apply_visitor(ErrorRenderer(code, message), result);
        return Status::Error;
    }

    // Decides whether to use the phantom candidates from big or small components if both are found.
    std::vector<PhantomNodeCandidates>
    SnapPhantomNodes(std::vector<PhantomCandidateAlternatives> alternatives_list) const
    {
        // are all phantoms from a tiny cc?
        const auto all_in_same_tiny_component =
            [](const std::vector<PhantomCandidateAlternatives> &alts_list) {
                return std::any_of(
                    alts_list.front().first.begin(),
                    alts_list.front().first.end(),
                    // For each of the first possible phantoms, check if all other
                    // positions in the list have a phantom from the same small component.
                    [&](const PhantomNode &phantom) {
                        if (!phantom.component.is_tiny)
                        {
                            return false;
                        }
                        const auto component_id = phantom.component.id;
                        return std::all_of(
                            std::next(alts_list.begin()),
                            std::end(alts_list),
                            [component_id](const PhantomCandidateAlternatives &alternatives) {
                                return candidatesHaveComponent(alternatives.first, component_id);
                            });
                    });
            };

        // Move the alternative into the final list
        const auto fallback_to_big_component = [](PhantomCandidateAlternatives &alternatives) {
            auto no_big_alternative = alternatives.second.empty();
            return no_big_alternative ? std::move(alternatives.first)
                                      : std::move(alternatives.second);
        };

        // Move the alternative into the final list
        const auto use_closed_phantom = [](PhantomCandidateAlternatives &alternatives) {
            return std::move(alternatives.first);
        };

        const auto no_alternatives =
            std::all_of(alternatives_list.begin(),
                        alternatives_list.end(),
                        [](const PhantomCandidateAlternatives &alternatives) {
                            return alternatives.second.empty();
                        });

        std::vector<PhantomNodeCandidates> snapped_phantoms;
        snapped_phantoms.reserve(alternatives_list.size());
        // The only case we don't snap to the big component if all phantoms are in the same small
        // component
        if (no_alternatives || all_in_same_tiny_component(alternatives_list))
        {
            std::transform(alternatives_list.begin(),
                           alternatives_list.end(),
                           std::back_inserter(snapped_phantoms),
                           use_closed_phantom);
        }
        else
        {
            std::transform(alternatives_list.begin(),
                           alternatives_list.end(),
                           std::back_inserter(snapped_phantoms),
                           fallback_to_big_component);
        }

        return snapped_phantoms;
    }

    std::string NormalizeStreetName(const std::string &name) const
    {
        std::string normalized;
        normalized.reserve(name.size());

        for (const unsigned char character : name)
        {
            if (std::isalnum(character))
            {
                normalized.push_back(
                    static_cast<char>(std::tolower(character)));
            }
        }

        return normalized;
    }

    std::string
    GetPhantomStreetName(const datafacade::BaseDataFacade &facade,
                         const PhantomNode &phantom) const
    {
        const auto segment_id =
            phantom.forward_segment_id.enabled
                ? phantom.forward_segment_id.id
                : phantom.reverse_segment_id.id;

        return std::string(
            facade.GetNameForID(facade.GetNameIndex(segment_id)));
    }

    bool
    PhantomMatchesStreet(const datafacade::BaseDataFacade &facade,
                         const PhantomNode &phantom,
                         const std::string &normalized_expected_street) const
    {
        return NormalizeStreetName(GetPhantomStreetName(facade, phantom)) ==
               normalized_expected_street;
    }

    // Falls back to default_radius for non-set radii
    std::vector<std::vector<PhantomNodeWithDistance>>
    GetPhantomNodesInRange(const datafacade::BaseDataFacade &facade,
                           const api::BaseParameters &parameters,
                           const std::vector<double> &radiuses,
                           bool use_all_edges = false) const
    {
        std::vector<std::vector<PhantomNodeWithDistance>> phantom_nodes(
            parameters.coordinates.size());
        BOOST_ASSERT(radiuses.size() == parameters.coordinates.size());

        const bool use_hints = !parameters.hints.empty();
        const bool use_bearings = !parameters.bearings.empty();
        const bool use_approaches = !parameters.approaches.empty();

        for (const auto i : util::irange<std::size_t>(0UL, parameters.coordinates.size()))
        {
            if (use_hints && parameters.hints[i] && !parameters.hints[i]->segment_hints.empty() &&
                parameters.hints[i]->IsValid(parameters.coordinates[i], facade))
            {
                for (const auto &seg_hint : parameters.hints[i]->segment_hints)
                {
                    phantom_nodes[i].push_back(PhantomNodeWithDistance{
                        seg_hint.phantom,
                        util::coordinate_calculation::greatCircleDistance(
                            parameters.coordinates[i], seg_hint.phantom.location)});
                }
                continue;
            }

            phantom_nodes[i] = facade.NearestPhantomNodesInRange(
                parameters.coordinates[i],
                radiuses[i],
                use_bearings ? parameters.bearings[i] : boost::none,
                use_approaches && parameters.approaches[i] ? parameters.approaches[i].get()
                                                           : engine::Approach::UNRESTRICTED,
                use_all_edges);
        }

        return phantom_nodes;
    }

    std::vector<std::vector<PhantomNodeWithDistance>>
    GetPhantomNodes(const datafacade::BaseDataFacade &facade,
                    const api::BaseParameters &parameters,
                    size_t number_of_results) const
    {
        std::vector<std::vector<PhantomNodeWithDistance>> phantom_nodes(
            parameters.coordinates.size());

        const bool use_hints = !parameters.hints.empty();
        const bool use_bearings = !parameters.bearings.empty();
        const bool use_radiuses = !parameters.radiuses.empty();
        const bool use_approaches = !parameters.approaches.empty();
        const bool use_street_names = !parameters.street_names.empty();

        BOOST_ASSERT(parameters.IsValid());
        for (const auto i : util::irange<std::size_t>(0UL, parameters.coordinates.size()))
        {
            if (use_hints && parameters.hints[i] && !parameters.hints[i]->segment_hints.empty() &&
                parameters.hints[i]->IsValid(parameters.coordinates[i], facade))
            {
                for (const auto &seg_hint : parameters.hints[i]->segment_hints)
                {
                    phantom_nodes[i].push_back(PhantomNodeWithDistance{
                        seg_hint.phantom,
                        util::coordinate_calculation::greatCircleDistance(
                            parameters.coordinates[i], seg_hint.phantom.location)});
                }
                continue;
            }

            

            const auto approach =
                use_approaches && parameters.approaches[i]
                    ? parameters.approaches[i].get()
                    : engine::Approach::UNRESTRICTED;

            const bool use_street_name =
                use_street_names && parameters.street_names[i] &&
                !parameters.street_names[i]->empty();

            if (use_street_name)
            {
                double search_radius = 200.0;

                if (use_radiuses && parameters.radiuses[i])
                {
                    search_radius = parameters.radiuses[i].get();
                }

                auto street_candidates =
                    facade.NearestPhantomNodesInRange(
                        parameters.coordinates[i],
                        search_radius,
                        use_bearings ? parameters.bearings[i] : boost::none,
                        approach,
                        false);

                const auto normalized_expected_street =
                    NormalizeStreetName(parameters.street_names[i].get());

                const auto matches_expected_street =
                    [&](const PhantomNodeWithDistance &candidate)
                {
                    return PhantomMatchesStreet(
                        facade,
                        candidate.phantom_node,
                        normalized_expected_street);
                };

                const bool found_matching_street =
                    std::any_of(street_candidates.begin(),
                                street_candidates.end(),
                                matches_expected_street);

                if (found_matching_street)
                {
                    const auto candidate_distance =
                        [&](const PhantomNodeWithDistance &candidate)
                    {
                        return util::coordinate_calculation::greatCircleDistance(
                            parameters.coordinates[i],
                            candidate.phantom_node.location);
                    };

                    std::stable_sort(
                        street_candidates.begin(),
                        street_candidates.end(),
                        [&](const PhantomNodeWithDistance &lhs,
                            const PhantomNodeWithDistance &rhs)
                        {
                            const bool lhs_matches =
                                matches_expected_street(lhs);
                            const bool rhs_matches =
                                matches_expected_street(rhs);

                            if (lhs_matches != rhs_matches)
                            {
                                return lhs_matches;
                            }

                            return candidate_distance(lhs) <
                                   candidate_distance(rhs);
                        });

                    if (street_candidates.size() > number_of_results)
                    {
                        street_candidates.resize(number_of_results);
                    }

                    phantom_nodes[i] = std::move(street_candidates);
                }
                else
                {
                    phantom_nodes[i] = facade.NearestPhantomNodes(
                        parameters.coordinates[i],
                        number_of_results,
                        use_radiuses ? parameters.radiuses[i] : boost::none,
                        use_bearings ? parameters.bearings[i] : boost::none,
                        approach);
                }
            }
            else
            {
                phantom_nodes[i] = facade.NearestPhantomNodes(
                    parameters.coordinates[i],
                    number_of_results,
                    use_radiuses ? parameters.radiuses[i] : boost::none,
                    use_bearings ? parameters.bearings[i] : boost::none,
                    approach);
            }

            // we didn't find a fitting node, return error
            if (phantom_nodes[i].empty())
            {
                break;
            }
        }
        return phantom_nodes;
    }

    std::vector<PhantomCandidateAlternatives>
    GetPhantomNodes(const datafacade::BaseDataFacade &facade,
                    const api::BaseParameters &parameters) const
    {
        std::vector<PhantomCandidateAlternatives> alternatives(parameters.coordinates.size());

        const bool use_hints = !parameters.hints.empty();
        const bool use_bearings = !parameters.bearings.empty();
        const bool use_radiuses = !parameters.radiuses.empty();
        const bool use_approaches = !parameters.approaches.empty();
        const bool use_all_edges = parameters.snapping == api::BaseParameters::SnappingType::Any;
        const bool use_street_names = !parameters.street_names.empty();

        BOOST_ASSERT(parameters.IsValid());
        for (const auto i : util::irange<std::size_t>(0UL, parameters.coordinates.size()))
        {
            if (use_hints && parameters.hints[i] && !parameters.hints[i]->segment_hints.empty() &&
                parameters.hints[i]->IsValid(parameters.coordinates[i], facade))
            {
                std::transform(parameters.hints[i]->segment_hints.begin(),
                               parameters.hints[i]->segment_hints.end(),
                               std::back_inserter(alternatives[i].first),
                               [](const auto &seg_hint) { return seg_hint.phantom; });
                // we don't set the second one - it will be marked as invalid
                continue;
            }

            const auto approach =
                use_approaches && parameters.approaches[i]
                    ? parameters.approaches[i].get()
                    : engine::Approach::UNRESTRICTED;

            const bool use_street_name =
                use_street_names && parameters.street_names[i] &&
                !parameters.street_names[i]->empty();

            bool selected_street_candidate = false;

            if (use_street_name)
            {
                double search_radius = 200.0;

                if (use_radiuses && parameters.radiuses[i])
                {
                    search_radius = parameters.radiuses[i].get();
                }

                const auto street_candidates =
                    facade.NearestPhantomNodesInRange(
                        parameters.coordinates[i],
                        search_radius,
                        use_bearings ? parameters.bearings[i] : boost::none,
                        approach,
                        use_all_edges);

                const auto normalized_expected_street =
                    NormalizeStreetName(parameters.street_names[i].get());

                auto best_match = street_candidates.end();

                for (auto candidate = street_candidates.begin();
                     candidate != street_candidates.end();
                     ++candidate)
                {
                    if (!PhantomMatchesStreet(
                            facade,
                            candidate->phantom_node,
                            normalized_expected_street))
                    {
                        continue;
                    }

                    if (best_match == street_candidates.end())
                    {
                        best_match = candidate;
                        continue;
                    }

                    const auto candidate_distance =
                        util::coordinate_calculation::greatCircleDistance(
                            parameters.coordinates[i],
                            candidate->phantom_node.location);

                    const auto best_distance =
                        util::coordinate_calculation::greatCircleDistance(
                            parameters.coordinates[i],
                            best_match->phantom_node.location);

                    if (candidate_distance < best_distance)
                    {
                        best_match = candidate;
                    }
                }

                if (best_match != street_candidates.end())
                {
                    const auto selected_location =
                        best_match->phantom_node.location;

                    for (const auto &candidate : street_candidates)
                    {
                        if (candidate.phantom_node.location ==
                                selected_location &&
                            PhantomMatchesStreet(
                                facade,
                                candidate.phantom_node,
                                normalized_expected_street))
                        {
                            alternatives[i].first.push_back(
                                candidate.phantom_node);
                        }
                    }

                    selected_street_candidate =
                        !alternatives[i].first.empty();
                }
            }

            if (!selected_street_candidate)
            {
                alternatives[i] =
                    facade.NearestCandidatesWithAlternativeFromBigComponent(
                        parameters.coordinates[i],
                        use_radiuses ? parameters.radiuses[i] : boost::none,
                        use_bearings ? parameters.bearings[i] : boost::none,
                        approach,
                        use_all_edges);
            }

            // we didn't find a fitting node, return error
            if (alternatives[i].first.empty())
            {
                // This ensures the list of phantom nodes only consists of valid nodes.
                // We can use this on the call-site to detect an error.
                alternatives.pop_back();
                break;
            }

            BOOST_ASSERT(!alternatives[i].first.empty());
        }
        return alternatives;
    }

    std::string
    MissingPhantomErrorMessage(const std::vector<PhantomCandidateAlternatives> &alternatives,
                               const std::vector<util::Coordinate> &coordinates) const
    {
        BOOST_ASSERT(alternatives.size() < coordinates.size());
        auto mismatch =
            std::mismatch(alternatives.begin(),
                          alternatives.end(),
                          coordinates.begin(),
                          coordinates.end(),
                          [](const auto &candidates_pair, const auto &coordinate) {
                              return std::any_of(candidates_pair.first.begin(),
                                                 candidates_pair.first.end(),
                                                 [&](const auto &phantom) {
                                                     return phantom.input_location == coordinate;
                                                 });
                          });
        std::size_t missing_index = std::distance(alternatives.begin(), mismatch.first);
        return std::string("Could not find a matching segment for coordinate ") +
               std::to_string(missing_index);
    }
};
} // namespace plugins
} // namespace engine
} // namespace osrm

#endif /* BASE_PLUGIN_HPP */
