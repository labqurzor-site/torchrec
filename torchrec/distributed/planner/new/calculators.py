#!/usr/bin/env python3

from typing import Dict, Optional, Tuple, List

from torchrec.distributed.planner.new.constants import (
    BIGINT_DTYPE,
    INTRA_NODE_BANDWIDTH,
    CROSS_NODE_BANDWIDTH,
    kernel_bw_lookup,
)
from torchrec.distributed.planner.new.types import (
    PlannerConstraints,
    Calculator,
    Topology,
    ShardingOption,
)
from torchrec.distributed.types import ShardingType


def cost_func_emb_wall_time(
    shard_lengths: List[List[int]],
    compute_kernel: str,
    compute_device: str,
    sharding_type: str,
    batch_size: int,
    world_size: int,
    local_world_size: int,
    input_lengths: List[float],
    input_data_type_size: float,
    output_data_type_size: float,
    bw_intra_host: int,
    bw_inter_host: int,
    has_input_dist: bool = True,
    has_output_dist: bool = True,
    caching_ratio: Optional[float] = None,
) -> List[float]:
    """
    Attempts to model costs as a function of relative wall times.
    Only models forward costs (ignores backward costs)
    The computation cost estimation is based on EmbeddingBagCollectionSharder
    (pooledEmbedding)
    Note: the computation of the output cost will count len(input_length) times due to pooling.

    Args:
        shard_lengths (List[List[int]]): the list of (local_rows, local_cols) pf each shard.
        compute_kernel (str): comput kernel.
        compute_device (str): compute device.
        sharding_type (str): tw, rw, cw, twrw, dp.
        batch_size (int): the size of each batch.
        world_size (int): the number of devices for all hosts.
        local_world_size (int): the number of the device for each host.
        input_lengths (List[float]): the list of the average number of lookups of each input query feature.
        input_data_type_size (float): the data type size of the distributed data_parallel input.
        output_data_type_size (float): the data type size of the distributed data_parallel output.
        bw_intra_host (int): the bandwidth within the single host like multiple threads.
        bw_inter_host (int): the bandwidth between two hosts like multiple machines.
        has_input_dist (bool = True): if we need input distributed.
        has_output_dist (bool = True): if we need output distributed.
        caching_ratio (Optional[float] = None): cache ratio to determine the bandwidth of device.

    Returns:
        List[float]: the list of cost for each shard.
    """
    shard_costs = []
    B = 1.0 * world_size * batch_size  # global batch size
    device_bw = kernel_bw_lookup(compute_device, compute_kernel, caching_ratio)

    for hash_size, emb_dim in shard_lengths:

        if sharding_type is ShardingType.TABLE_WISE.value:
            input_cost, compute_cost, output_cost = _get_tw_sharding_cost(
                B,
                world_size,
                input_lengths,
                emb_dim,
                input_data_type_size,
                output_data_type_size,
                device_bw,
                bw_inter_host,
            )
        elif sharding_type is ShardingType.COLUMN_WISE.value:
            input_cost, compute_cost, output_cost = _get_cw_sharding_cost(
                B,
                world_size,
                input_lengths,
                emb_dim,
                input_data_type_size,
                output_data_type_size,
                device_bw,
                bw_inter_host,
            )
        elif sharding_type is ShardingType.ROW_WISE.value:
            input_cost, compute_cost, output_cost = _get_rw_sharding_cost(
                B,
                world_size,
                input_lengths,
                emb_dim,
                input_data_type_size,
                output_data_type_size,
                device_bw,
                bw_inter_host,
            )
        elif sharding_type is ShardingType.TABLE_ROW_WISE.value:
            input_cost, compute_cost, output_cost = _get_twrw_sharding_cost(
                B,
                world_size,
                local_world_size,
                input_lengths,
                emb_dim,
                input_data_type_size,
                output_data_type_size,
                device_bw,
                bw_inter_host,
                bw_intra_host,
            )
        elif sharding_type is ShardingType.DATA_PARALLEL.value:
            input_cost, compute_cost, output_cost = _get_dp_sharding_cost(
                batch_size,
                input_lengths,
                hash_size * emb_dim,
                bw_inter_host,
                emb_dim,
                output_data_type_size,
                device_bw,
            )
        else:
            raise RuntimeError(f"Unexpected sharding type: {sharding_type}")

        shard_cost = 0
        shard_cost += input_cost if has_input_dist else 0
        shard_cost += compute_cost
        shard_cost += output_cost if has_output_dist else 0
        shard_costs.append(shard_cost)

    return shard_costs


def _get_tw_sharding_cost(
    global_batch_size: float,
    world_size: int,
    input_lengths: List[float],
    emb_dim: int,
    input_data_type_size: float,
    output_data_type_size: float,
    device_bw: float,
    bw_inter_host: int,
) -> Tuple[float, float, float]:
    input_cost = (
        global_batch_size * sum(input_lengths) * input_data_type_size / bw_inter_host
    )
    compute_cost = (
        global_batch_size
        * sum(input_lengths)
        * emb_dim
        * output_data_type_size
        / device_bw
    )
    output_cost = (
        global_batch_size
        * emb_dim
        * len(input_lengths)
        * output_data_type_size
        / bw_inter_host
    )
    return (input_cost, compute_cost, output_cost)


def _get_cw_sharding_cost(
    global_batch_size: float,
    world_size: int,
    input_lengths: List[float],
    emb_dim: int,
    input_data_type_size: float,
    output_data_type_size: float,
    device_bw: float,
    bw_inter_host: int,
) -> Tuple[float, float, float]:
    input_cost = (
        global_batch_size * sum(input_lengths) * input_data_type_size / bw_inter_host
    )
    compute_cost = (
        global_batch_size
        * sum(input_lengths)
        * emb_dim
        * output_data_type_size
        / device_bw
    )
    output_cost = (
        global_batch_size
        * emb_dim
        * len(input_lengths)
        * output_data_type_size
        / bw_inter_host
    )
    return (input_cost, compute_cost, output_cost)


def _get_rw_sharding_cost(
    global_batch_size: float,
    world_size: int,
    input_lengths: List[float],
    emb_dim: int,
    input_data_type_size: float,
    output_data_type_size: float,
    device_bw: float,
    bw_inter_host: int,
) -> Tuple[float, float, float]:
    input_cost = (
        global_batch_size
        * sum(input_lengths)
        / world_size
        * input_data_type_size
        / bw_inter_host
    )
    compute_cost = (
        global_batch_size
        * sum(input_lengths)
        / world_size
        * emb_dim
        * output_data_type_size
        / device_bw
    )
    output_cost = (
        global_batch_size
        * emb_dim
        * len(input_lengths)
        * output_data_type_size
        / bw_inter_host
    )
    return (input_cost, compute_cost, output_cost)


def _get_twrw_sharding_cost(
    global_batch_size: float,
    world_size: int,
    local_world_size: int,
    input_lengths: List[float],
    emb_dim: int,
    input_data_type_size: float,
    output_data_type_size: float,
    device_bw: float,
    bw_inter_host: int,
    bw_intra_host: int,
) -> Tuple[float, float, float]:
    input_cost = (
        global_batch_size
        * sum(input_lengths)
        / local_world_size
        * input_data_type_size
        / bw_inter_host
    )
    compute_cost = (
        global_batch_size
        * sum(input_lengths)
        / local_world_size
        * emb_dim
        * output_data_type_size
        / device_bw
    )
    output_cost = (
        global_batch_size
        * emb_dim
        * len(input_lengths)
        * output_data_type_size
        / bw_intra_host
        + global_batch_size
        * emb_dim
        * len(input_lengths)
        * output_data_type_size
        * (local_world_size / world_size)
        / bw_inter_host
    )
    return (input_cost, compute_cost, output_cost)


def _get_dp_sharding_cost(
    batch_size: float,
    input_lengths: List[float],
    grad_num_elem: int,
    bw_inter_host: int,
    emb_dim: int,
    output_data_type_size: float,
    device_bw: float,
) -> Tuple[float, float, float]:
    input_cost = 0
    compute_cost = (
        batch_size * sum(input_lengths) * emb_dim * output_data_type_size / device_bw
    )
    # TODO: this is allreduce cost, better separated out as backward cost
    output_cost = grad_num_elem * output_data_type_size / bw_inter_host
    return (input_cost, compute_cost, output_cost)


class EmbeddingWTCostCalculator(Calculator):
    """
    Calculate embedding wall time cost for given topology and constraints.

    Constructor Args:
        topology (Topology): device topology.
        constraints (Optional[Dict[str, PlannerConstraints]]): dict of parameter name
            to provided PlannerConstraints.

    """

    def __init__(
        self,
        topology: Topology,
        constraints: Optional[Dict[str, PlannerConstraints]] = None,
    ) -> None:
        self._topology = topology
        self._constraints = constraints

    def run(self, sharding_options: List[ShardingOption]) -> None:

        """
        Generates the list of costs for each shard for each sharding_option.

        Args:
            sharding_options: list of ShardingOption.

        """
        for sharding_option in sharding_options:
            caching_ratio = (
                self._constraints[sharding_option.name].caching_ratio
                if self._constraints and self._constraints.get(sharding_option.name)
                else None
            )
            shard_costs = cost_func_emb_wall_time(
                shard_lengths=[shard.length for shard in sharding_option.shards],
                compute_kernel=sharding_option.compute_kernel,
                compute_device=self._topology.compute_device,
                sharding_type=sharding_option.sharding_type,
                batch_size=sharding_option.batch_size,
                world_size=self._topology.world_size,
                local_world_size=self._topology.local_world_size,
                input_lengths=sharding_option.input_lengths,
                input_data_type_size=BIGINT_DTYPE,
                output_data_type_size=sharding_option.tensor.element_size(),
                bw_intra_host=getattr(
                    self._topology, "intra_host_bw", INTRA_NODE_BANDWIDTH
                ),
                bw_inter_host=getattr(
                    self._topology, "inter_host_bw", CROSS_NODE_BANDWIDTH
                ),
                has_input_dist=True if sharding_option.upstream_modules else False,
                has_output_dist=False if sharding_option.downstream_modules else True,
                caching_ratio=caching_ratio,
            )
            for shard, cost in zip(sharding_option.shards, shard_costs):
                shard.cost = cost
