===========================
Storage backend migration
===========================

:program:`cloudkitty-storage-migrate` copies rated data between different v2
storage backends. The backend configured in ``[storage] backend`` is always the
destination, and ``--migrate-from`` selects the source. The CloudKitty
configuration used by the command must contain connection details for both
drivers. For example, an InfluxDB to OpenSearch migration requires a
configuration similar to:

.. code-block:: ini

   [storage]
   version = 2
   backend = opensearch # This is the destination backend

   [storage_influxdb]
   host = influx.example.com
   database = cloudkitty
   # Other required InfluxDB connection options

   [storage_opensearch]
   host = https://opensearch.example.com:9200
   index_name = cloudkitty
   # Other required OpenSearch connection options

Stop all services that write rated data, such as every
:program:`cloudkitty-processor` instance, before changing the active backend.
Ensure that the destination backend is provisioned before running the
migration. For example, in OpenSearch, the configured index must already exist.
After changing the configuration to define the destination as the active
storage backend, run :command:`cloudkitty-storage-init` to perform the
initialization and validation supported by that backend. For OpenSearch, this
applies the CloudKitty mapping to the existing index:

.. code-block:: console

   $ cloudkitty-storage-init

You are now ready to use :command:`cloudkitty-storage-migrate`. The simplest
invocation discovers the complete time range from the source and migrates it to
the configured destination:

.. code-block:: console

   $ cloudkitty-storage-migrate --migrate-from influxdb

Use ``--begin`` and ``--end`` to migrate a specific time range. The begin value
is inclusive and the end value is exclusive, ``[begin, end)``. When only one
bound is provided, the other is discovered from the source:

.. code-block:: console

   $ cloudkitty-storage-migrate --migrate-from influxdb \
       --begin 2026-01-01T00:00:00Z --end 2026-02-01T00:00:00Z

If ``--begin`` is omitted, the migration starts at the beginning of the first
dataframe stored in the source backend:

.. code-block:: console

   $ cloudkitty-storage-migrate --migrate-from influxdb \
       --end 2026-02-01T00:00:00Z

If ``--end`` is omitted, the migration finishes at the end of the last
dataframe stored in the source backend:

.. code-block:: console

   $ cloudkitty-storage-migrate --migrate-from influxdb \
       --begin 2026-01-01T00:00:00Z

Use ``--page-size`` to control the maximum number of stored records retrieved
from the source in each copy operation. The default is 1000. A smaller value
reduces the memory and request size used by each operation, while a larger
value may improve migration throughput by requiring fewer requests:

.. code-block:: console

   $ cloudkitty-storage-migrate --migrate-from influxdb --page-size 5000

The destination must be empty within the migration time range by default. If
the destination already contains data in that range, either clear it separately
or explicitly delete it with ``--overwrite-destination``. This deletes
destination data only within the effective migration range before copying and
verifying the source data:

.. code-block:: console

   $ cloudkitty-storage-migrate --migrate-from influxdb \
       --overwrite-destination

Restart the services that write rated data only after the command completes
successfully. Run ``cloudkitty-storage-migrate --help`` for all available
options.
