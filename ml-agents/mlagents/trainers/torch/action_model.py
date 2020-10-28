import abc
from typing import List, Tuple
from mlagents.torch_utils import torch, nn
import numpy as np
import math
from mlagents.trainers.torch.layers import linear_layer, Initialization
from mlagents.trainers.torch.distributions import (
    DistInstance,
    DiscreteDistInstance,
    GaussianDistribution,
    MultiCategoricalDistribution,
)

from mlagents.trainers.torch.utils import ModelUtils
from mlagents_envs.base_env import ActionSpec

EPSILON = 1e-7  # Small value to avoid divide by zero


class ActionModel(nn.Module):
    def __init__(
        self,
        hidden_size: int,
        action_spec: ActionSpec,
        conditional_sigma: bool = False,
        tanh_squash: bool = False,
    ):
        super().__init__()
        self.encoding_size = hidden_size
        self.continuous_act_size = action_spec.continuous_action_size
        self.discrete_act_branches = action_spec.discrete_action_branches
        self.discrete_act_size = action_spec.discrete_action_size
        self.action_spec = action_spec

        self._split_list: List[int] = []
        self._distributions = torch.nn.ModuleList()
        if self.continuous_act_size > 0:
            self._distributions.append(
                GaussianDistribution(
                    self.encoding_size,
                    self.continuous_act_size,
                    conditional_sigma=conditional_sigma,
                    tanh_squash=tanh_squash,
                )
            )
            self._split_list.append(self.continuous_act_size)

        if self.discrete_act_size > 0:
            self._distributions.append(
                MultiCategoricalDistribution(
                    self.encoding_size, self.discrete_act_branches
                )
            )
            self._split_list += [1 for _ in range(self.discrete_act_size)]

    def _sample_action(self, dists: List[DistInstance]) -> List[torch.Tensor]:
        """
        Samples actions from list of distribution instances
        """
        actions = []
        for action_dist in dists:
            action = action_dist.sample()
            actions.append(action)
        return actions

    def _get_dists(
        self, inputs: torch.Tensor, masks: torch.Tensor
    ) -> Tuple[List[DistInstance], List[DiscreteDistInstance]]:
        distribution_instances: List[DistInstance] = []
        for distribution in self._distributions:
            dist_instances = distribution(inputs, masks)
            for dist_instance in dist_instances:
                distribution_instances.append(dist_instance)
        return distribution_instances

    def evaluate(
        self, inputs: torch.Tensor, masks: torch.Tensor, actions: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        dists = self._get_dists(inputs, masks)
        split_actions = torch.split(actions, self._split_list, dim=1)
        action_lists: List[torch.Tensor] = []
        for split_action in split_actions:
            action_list = [split_action[..., i] for i in range(split_action.shape[-1])]
            action_lists += action_list
        log_probs, entropies, _ = ModelUtils.get_probs_and_entropy(action_lists, dists)
        return log_probs, entropies

    def get_action_out(self, inputs: torch.Tensor, masks: torch.Tensor) -> torch.Tensor:
        dists = self._get_dists(inputs, masks)
        return (
            dists[0].exported_model_output(),
            dists[1].exported_model_output(),
            torch.cat([dist.exported_model_output() for dist in dists], dim=1),
        )

    def forward(
        self, inputs: torch.Tensor, masks: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        dists = self._get_dists(inputs, masks)
        action_outs: List[torch.Tensor] = []
        action_lists = self._sample_action(dists)
        for action_list, dist in zip(action_lists, dists):
            action_out = action_list.unsqueeze(-1)
            action_outs.append(dist.structure_action(action_out))
        log_probs, entropies, _ = ModelUtils.get_probs_and_entropy(action_lists, dists)
        action = torch.cat(action_outs, dim=1)
        return (action, log_probs, entropies)
