# Copyright 2025 Red Hat
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
from collections import Counter
import datetime

from oslo_log import log
import requests

from cloudkitty.storage.v2.loki import exceptions
from cloudkitty.utils import json

LOG = log.getLogger(__name__)


class LokiClient(object):
    """Class used to ease interaction with Loki."""

    MAX_QUERY_LIMIT = 5000

    # Fields stored as structured metadata for indexed querying (Loki 3.0+)
    INDEXED_FIELDS = {'type', 'user', 'unit', 'flavor_name'}

    def __init__(self, url, tenant, stream_labels, content_type, buffer_size,
                 shard_days, cert, verify, timeout,
                 use_structured_metadata=False):
        if content_type != "application/json":
            raise exceptions.UnsupportedContentType(content_type)

        self._base_url = url.strip('/')
        self._stream_labels = stream_labels
        self._headers = {
            'X-Scope-OrgID': tenant,
            'Content-Type': content_type
        }
        self._buffer_size = buffer_size
        self._points = {}
        self._shard_days = shard_days

        self._cert = cert
        self._verify = verify
        self._use_structured_metadata = use_structured_metadata

        self.timeout = timeout

    def _build_payload_json(self, batch):
        """Build payload with separate streams per project_id."""
        streams = []
        for project_id, values in batch.items():
            # Combine base stream labels with project_id
            stream_labels = self._stream_labels.copy()
            # Only add project_id label if it's not None
            if project_id is not None:
                stream_labels['project_id'] = project_id
            streams.append({
                "stream": stream_labels,
                "values": values
            })

        payload = {"streams": streams}
        return payload

    def _dict_to_loki_query(self, tags_dict, groupby=False, brackets=True):
        """Converts from Python dict to Loki query language."""
        if not tags_dict:
            return '{}'

        pairs = []
        for key, value in tags_dict.items():
            if isinstance(value, list):
                value = value[0]
            if isinstance(value, str):
                value = value.replace('"', '\\"')
            if groupby:
                pairs.append(f'groupby_{key}="{value}"')
            else:
                pairs.append(f'{key}="{value}"')

        if brackets:
            return '{' + ', '.join(pairs) + '}'
        else:
            return ', '.join(pairs)

    def _base_query(self, project_id=None):
        """Build base query for Loki.

        When use_structured_metadata is disabled, appends '| json' to parse
        log lines. When enabled, callers handle adding '| json' as needed.
        """
        stream_labels = self._stream_labels.copy()
        if project_id is not None:
            stream_labels['project_id'] = project_id
        query = self._dict_to_loki_query(stream_labels)

        if not self._use_structured_metadata:
            query += ' | json'

        return query

    def _build_query(self, filters, metric_types=None):
        """Build a Loki query from filters and optional metric_types.

        Handles extracting project_id from filters, and when structured
        metadata is enabled, splits filters into indexed (metadata) and
        non-indexed (json-parsed) categories for optimal query performance.

        Returns the assembled Loki query string.
        """
        project_id = None
        remaining_filters = filters
        if filters and 'project_id' in filters:
            project_id = filters['project_id']
            remaining_filters = {k: v for k, v in filters.items()
                                 if k != 'project_id'}

        if self._use_structured_metadata:
            query = self._base_query(project_id=project_id)
            metadata_filters = []
            json_filters = []

            # Handle metric_types filter (type is indexed)
            if metric_types:
                if isinstance(metric_types, list):
                    current_metric_type = metric_types[0]
                else:
                    current_metric_type = metric_types
                metadata_filters.append(f'type="{current_metric_type}"')

            # Split remaining filters into indexed and non-indexed
            if remaining_filters:
                for key, value in remaining_filters.items():
                    if isinstance(value, list):
                        value = value[0]
                    if isinstance(value, str):
                        value = value.replace('"', '\\"')
                    if key in self.INDEXED_FIELDS:
                        metadata_filters.append(f'{key}="{value}"')
                    else:
                        json_filters.append(f'groupby_{key}="{value}"')

            # Add metadata filters first (before | json)
            for mf in metadata_filters:
                query += ' | ' + mf

            # Add | json and non-indexed filters if needed
            if json_filters:
                query += ' | json'
                for jf in json_filters:
                    query += ' | ' + jf
            else:
                # Need | json so total() can access qty/price from stream
                query += ' | json'
        else:
            query = self._base_query(project_id=project_id)
            loki_query_parts = []

            if remaining_filters:
                loki_query_parts.append(
                    self._dict_to_loki_query(
                        remaining_filters, groupby=True, brackets=False
                    )
                )

            if metric_types:
                if isinstance(metric_types, list):
                    current_metric_type = metric_types[0]
                else:
                    current_metric_type = metric_types
                loki_query_parts.append(f'type = "{current_metric_type}"')

            if loki_query_parts:
                query += ' | ' + ', '.join(loki_query_parts)

        return query

    @staticmethod
    def _timestamp_ns(value):
        if isinstance(value, int):
            return value
        return int(value.timestamp() * 1_000_000_000)

    def search(self, query, begin, end, limit, direction=None):
        url = f"{self._base_url}/query_range"

        if query is None:
            query = self._base_query()

        params = {
            "query": query,
            "start": self._timestamp_ns(begin),
            "end": self._timestamp_ns(end),
            "limit": limit
        }
        if direction is not None:
            params['direction'] = direction

        LOG.debug("Executing Loki query: %s", params)
        response = requests.get(url, params=params, headers=self._headers,
                                cert=self._cert, verify=self._verify,
                                timeout=self.timeout)

        if response.status_code == 200:
            data = response.json()['data']
        else:
            LOG.error(
                "Failed to query logs or empty result: %d - %s",
                response.status_code, response.text
            )
            raise exceptions.QueryError(
                '{} - {}'.format(response.status_code, response.text))
        return data

    def push(self):
        """Send messages to Loki in batches."""
        url = f"{self._base_url}/push"

        if not self._points:
            return

        # Count total points across all project_ids
        total_points = sum(len(points) for points in self._points.values())

        payload = self._build_payload_json(self._points)
        response = requests.post(url, json=payload, headers=self._headers,
                                 cert=self._cert, verify=self._verify,
                                 timeout=self.timeout)

        if response.status_code == 204:
            LOG.debug(
                "Batch of %d messages across %d project(s) "
                "pushed successfully.",
                total_points, len(self._points),
            )
            self._points = {}
        else:
            LOG.error(
                "Failed to push logs: %d - %s",
                response.status_code, response.text
            )
            raise exceptions.PushError(
                response.status_code, response.text)

    def delete_by_query(self, query, begin, end):
        url = f"{self._base_url}/delete"

        if query is None:
            query = self._base_query()

        if end is None:
            end = datetime.datetime.now(datetime.timezone.utc)

        params = {
            "query": query,
            "start": int(begin.timestamp()),
            "end": int(end.timestamp()),
        }

        response = requests.post(url, params=params, headers=self._headers,
                                 cert=self._cert, verify=self._verify,
                                 timeout=self.timeout)

        if response.status_code == 204:
            LOG.debug(
                "Dataframes deleted successfully."
            )
        else:
            LOG.error(
                "Failed to delete dataframes: %d - %s",
                response.status_code, response.text
            )

    def delete(self, begin, end, filters):
        query = self._build_query(filters)
        self.delete_by_query(query, begin, end)

    @staticmethod
    def _flatten_streams(streams):
        output = []
        for item in streams:
            if not isinstance(item, dict):
                continue
            for value in item.get('values', []):
                output.append({
                    'stream': item.get('stream', {}),
                    'values': [value],
                })
        output.sort(key=LokiClient._entry_timestamp)
        return output

    @staticmethod
    def _entry_timestamp(entry):
        return int(entry['values'][0][0])

    @staticmethod
    def _entry_key(entry):
        return (
            LokiClient._entry_timestamp(entry),
            entry['values'][0][1],
            tuple(sorted(entry.get('stream', {}).items())),
        )

    def _retrieve(self, begin, end, filters, metric_types, limit,
                  direction=None):
        """Retrieves dataframes stored in Loki."""
        query = self._build_query(filters, metric_types=metric_types)

        LOG.debug(
            "Loki query: '%s', begin: '%s', end: '%s', limit: '%s'",
            query, begin, end, limit
        )

        data_response = self.search(
            query, begin, end, limit, direction=direction)

        if not isinstance(data_response, dict) or \
           'stats' not in data_response or \
           'result' not in data_response:
            LOG.warning(
                "Data from Loki search is not in the expected dictionary "
                "format or is missing keys. Query: '%s'. Response "
                "received: %s",
                query, data_response
            )
            raise exceptions.QueryError(data_response)

        output = self._flatten_streams(data_response.get('result', []))

        return len(output), output

    def _time_ranges(self, begin, end):
        step = datetime.timedelta(days=self._shard_days)
        current_begin = begin
        while current_begin < end:
            current_end = min(current_begin + step, end)
            yield current_begin, current_end
            current_begin = current_end

    def _iter_retrieve_range(self, begin, end, filters, metric_types,
                             query_limit):
        cursor = self._timestamp_ns(begin)
        end_ns = self._timestamp_ns(end)
        seen_at_cursor = Counter()

        while cursor < end_ns:
            _total, entries = self._retrieve(
                cursor, end_ns, filters, metric_types, query_limit,
                direction='forward')
            if not entries:
                break

            remaining_seen = seen_at_cursor.copy()
            new_entries = []
            for entry in entries:
                key = self._entry_key(entry)
                if self._entry_timestamp(entry) == cursor \
                        and remaining_seen[key]:
                    remaining_seen[key] -= 1
                else:
                    new_entries.append(entry)

            if new_entries:
                yield new_entries

            if len(entries) < query_limit:
                break

            last_timestamp = self._entry_timestamp(entries[-1])
            if last_timestamp == cursor:
                if not new_entries:
                    raise exceptions.QueryLimitExceeded(
                        query_limit, cursor)
                seen_at_cursor.update(
                    self._entry_key(entry) for entry in new_entries)
            else:
                cursor = last_timestamp
                seen_at_cursor = Counter(
                    self._entry_key(entry) for entry in entries
                    if self._entry_timestamp(entry) == cursor)

    def iter_retrieve(self, begin, end, filters, metric_types,
                      query_limit=MAX_QUERY_LIMIT):
        for current_begin, current_end in self._time_ranges(begin, end):
            for entries in self._iter_retrieve_range(
                    current_begin, current_end, filters, metric_types,
                    query_limit):
                yield entries

    def retrieve(self, begin, end, filters, metric_types, limit,
                 offset=0, paginate=True):
        total = 0
        output = []

        for entries in self.iter_retrieve(
                begin, end, filters, metric_types):
            first = max(offset - total, 0)
            total += len(entries)
            if not paginate:
                output += entries
            elif first < len(entries) and len(output) < limit:
                remaining = limit - len(output)
                output += entries[first:first + remaining]

        return total, output

    def get_bounds(self, begin, end):
        output = []
        for current_begin, current_end in self._time_ranges(begin, end):
            for direction in ('forward', 'backward'):
                _total, entries = self._retrieve(
                    current_begin, current_end, None, None, 1,
                    direction=direction)
                if entries:
                    output.append(entries[0])
        return output

    def add_point(self, point, type, start, end):
        """Append a point to the client, grouped by project_id."""
        timestamp_ns = int(start.timestamp() * 1_000_000_000)
        timestamp = str(timestamp_ns)

        # Extract project_id from point.groupby (use None if not present)
        project_id = point.groupby.get('project_id', None)

        data = {
            'start': start,
            'end': end,
            'type': type,
            'unit': point.unit,
            'description': point.description,
            'qty': point.qty,
            'price': point.price,
            'groupby': point.groupby,
            'metadata': point.metadata,
        }

        log_line = json.dumps(data)

        # Build structured metadata from INDEXED_FIELDS for indexed querying
        field_sources = {
            'type': type,
            'unit': point.unit,
        }
        structured_metadata = {}
        for field in self.INDEXED_FIELDS:
            if field in field_sources:
                value = field_sources[field]
            else:
                value = point.groupby.get(field, '')
            if value:
                structured_metadata[field] = str(value)

        # Group points by project_id
        if project_id not in self._points:
            self._points[project_id] = []

        self._points[project_id].append(
            [timestamp, log_line, structured_metadata])

        # Check if any project has reached buffer size
        if any(len(points) >= self._buffer_size for points
               in self._points.values()):
            self.push()

    def total(self, begin, end, metric_types, filters, groupby,
              custom_fields, offset, limit, paginate):
        """Calculate total sum of 'price' and 'qty' for entries.

        This method calculates totals for entries that match the specified
        groupby value.
        """
        if custom_fields:
            LOG.warning(
                "'custom_fields' are not implemented yet for Loki. "
                "Therefore, the custom fields [%s] informed by the user "
                "will be ignored.", custom_fields
            )

        total_count, data = self.retrieve(
                begin, end, filters, metric_types, limit,
                paginate=False)

        if not groupby:
            total_qty = 0.0
            total_price = 0.0
            for item in data:
                stream = item.get('stream', {})
                qty = float(stream.get('qty', 0))
                price = float(stream.get('price', 0))

                total_qty += qty
                total_price += price

            return 1, [{
                'sum_qty': {'value': total_qty},
                'sum_price': {'value': total_price}
            }]

        grouped_data = {}
        for item in data:
            stream = item.get('stream', {})
            qty = float(stream.get('qty', 0))
            price = float(stream.get('price', 0))

            key_parts = {}
            for field in groupby:
                if self._use_structured_metadata \
                        and field in self.INDEXED_FIELDS:
                    # Indexed fields are available directly in stream
                    key_parts[field] = stream.get(field, '')
                elif field == 'type':
                    # type is always available directly (legacy behavior)
                    key_parts[field] = stream.get(field, '')
                else:
                    # Non-indexed fields have groupby_ prefix from JSON parsing
                    key_parts[field] = stream.get('groupby_' + field)

            key = tuple((k, v) for k, v in sorted(key_parts.items()))

            if key not in grouped_data:
                grouped_data[key] = {
                    'sum_qty': 0.0,
                    'sum_price': 0.0,
                    'key_parts': dict(key_parts)
                }

            grouped_data[key]['sum_qty'] += qty
            grouped_data[key]['sum_price'] += price

        result = []
        for _key_tuple, values in grouped_data.items():
            result.append({
                'key': values['key_parts'],
                'sum_qty': {'value': values['sum_qty']},
                'sum_price': {'value': values['sum_price']}
            })

        total = len(result)
        if paginate:
            result = result[offset:offset + limit]
        return total, result
