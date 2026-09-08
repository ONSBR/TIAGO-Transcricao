import copy
from typing import Optional

import torch
from pyannote.audio.core.task import Problem, Resolution, Specifications
from pyannote.audio.models.segmentation import PyanNet
from pyannote.audio.utils.loss import binary_cross_entropy, nll_loss
from pyannote.audio.utils.permutation import permutate
from pyannote.audio.utils.powerset import Powerset
from transformers import PretrainedConfig, PreTrainedModel

# Adapted from https://github.com/pyannote/pyannote-audio/blob/develop/pyannote/audio/models/segmentation/PyanNet.py
# MIT License
#
# Copyright (c) 2020 CNRS
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from functools import lru_cache
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F
from einops import rearrange
from pyannote.audio.core.task import Problem
from pyannote.audio.models.blocks.sincnet import SincNet
from pyannote.audio.utils.params import merge_dict
from pyannote.core.utils.generators import pairwise


class Dict(dict):
    def __init__(self, *args, **kwargs):
        super(Dict, self).__init__(*args, **kwargs)
        self.__dict__ = self


class PyanNet_nn(torch.nn.Module):
    """PyanNet segmentation model adapted from PyanNet model used in pyannote.
    Inherits from nn.Module (no more dependency to pytorch Lightning)
    Doesn't need the task attribute anymore.

    SincNet > LSTM > Feed forward > Classifier

    Parameters
    ----------
    sample_rate : int, optional
        Audio sample rate. Defaults to 16kHz (16000).
    num_channels : int, optional
        Number of channels. Defaults to mono (1).
    sincnet : dict, optional
        Keyword arugments passed to the SincNet block.
        Defaults to {"stride": 1}.
    lstm : dict, optional
        Keyword arguments passed to the LSTM layer.
        Defaults to {"hidden_size": 128, "num_layers": 2, "bidirectional": True},
        i.e. two bidirectional layers with 128 units each.
        Set "monolithic" to False to split monolithic multi-layer LSTM into multiple mono-layer LSTMs.
        This may proove useful for probing LSTM internals.
    linear : dict, optional
        Keyword arugments used to initialize linear layers
        Defaults to {"hidden_size": 128, "num_layers": 2},
        i.e. two linear layers with 128 units each.
    """

    SINCNET_DEFAULTS = {"stride": 10}
    LSTM_DEFAULTS = {
        "hidden_size": 128,
        "num_layers": 4,
        "bidirectional": True,
        "monolithic": True,
        "dropout": 0.0,
    }
    LINEAR_DEFAULTS = {"hidden_size": 128, "num_layers": 2}

    def __init__(
        self,
        sincnet: Optional[dict] = None,
        lstm: Optional[dict] = None,
        linear: Optional[dict] = None,
        sample_rate: int = 16000,
        num_channels: int = 1,
    ):
        super(PyanNet_nn, self).__init__()

        sincnet = merge_dict(self.SINCNET_DEFAULTS, sincnet)
        sincnet["sample_rate"] = sample_rate
        lstm = merge_dict(self.LSTM_DEFAULTS, lstm)
        lstm["batch_first"] = True
        linear = merge_dict(self.LINEAR_DEFAULTS, linear)

        self.hparams = Dict(
            {
                "linear": {"hidden_size": 128, "num_layers": 2},
                "lstm": {
                    "hidden_size": 128,
                    "num_layers": 4,
                    "bidirectional": True,
                    "monolithic": True,
                    "dropout": 0.0,
                    "batch_first": True,
                },
                "num_channels": 1,
                "sample_rate": 16000,
                "sincnet": {"stride": 10, "sample_rate": 16000},
            }
        )

        self.sincnet = SincNet(**self.hparams.sincnet)

        monolithic = lstm["monolithic"]
        if monolithic:
            multi_layer_lstm = dict(lstm)
            del multi_layer_lstm["monolithic"]
            self.lstm = nn.LSTM(60, **multi_layer_lstm)

        else:
            num_layers = lstm["num_layers"]
            if num_layers > 1:
                self.dropout = nn.Dropout(p=lstm["dropout"])

            one_layer_lstm = dict(lstm)
            one_layer_lstm["num_layers"] = 1
            one_layer_lstm["dropout"] = 0.0
            del one_layer_lstm["monolithic"]

            self.lstm = nn.ModuleList(
                [
                    nn.LSTM(
                        60 if i == 0 else lstm["hidden_size"] * (2 if lstm["bidirectional"] else 1),
                        **one_layer_lstm,
                    )
                    for i in range(num_layers)
                ]
            )

        if linear["num_layers"] < 1:
            return

        lstm_out_features: int = self.hparams.lstm["hidden_size"] * (2 if self.hparams.lstm["bidirectional"] else 1)
        self.linear = nn.ModuleList(
            [
                nn.Linear(in_features, out_features)
                for in_features, out_features in pairwise(
                    [
                        lstm_out_features,
                    ]
                    + [self.hparams.linear["hidden_size"]] * self.hparams.linear["num_layers"]
                )
            ]
        )

    @property
    def dimension(self) -> int:
        """Dimension of output"""
        if isinstance(self.specifications, tuple):
            raise ValueError("PyanNet does not support multi-tasking.")

        if self.specifications.powerset:
            return self.specifications.num_powerset_classes
        else:
            return len(self.specifications.classes)

    def default_activation(
        self,
    ):
        if self.specifications.problem == Problem.BINARY_CLASSIFICATION:
            return nn.Sigmoid()

        elif self.specifications.problem == Problem.MONO_LABEL_CLASSIFICATION:
            return nn.LogSoftmax(dim=-1)

        elif self.specifications.problem == Problem.MULTI_LABEL_CLASSIFICATION:
            return nn.Sigmoid()
        else:
            msg = "TODO: implement default activation for other types of problems"
            raise NotImplementedError(msg)

    def build(self):
        if self.hparams.linear["num_layers"] > 0:
            in_features = self.hparams.linear["hidden_size"]
        else:
            in_features = self.hparams.lstm["hidden_size"] * (2 if self.hparams.lstm["bidirectional"] else 1)

        self.classifier = nn.Linear(in_features, self.dimension)
        self.activation = self.default_activation()

    @lru_cache
    def num_frames(self, num_samples: int) -> int:
        """Compute number of output frames for a given number of input samples

        Parameters
        ----------
        num_samples : int
            Number of input samples

        Returns
        -------
        num_frames : int
            Number of output frames
        """

        return self.sincnet.num_frames(num_samples)

    def receptive_field_size(self, num_frames: int = 1) -> int:
        """Compute size of receptive field

        Parameters
        ----------
        num_frames : int, optional
            Number of frames in the output signal

        Returns
        -------
        receptive_field_size : int
            Receptive field size.
        """
        return self.sincnet.receptive_field_size(num_frames=num_frames)

    def receptive_field_center(self, frame: int = 0) -> int:
        """Compute center of receptive field

        Parameters
        ----------
        frame : int, optional
            Frame index

        Returns
        -------
        receptive_field_center : int
            Index of receptive field center.
        """

        return self.sincnet.receptive_field_center(frame=frame)

    def forward(self, waveforms: torch.Tensor) -> torch.Tensor:
        """Pass forward

        Parameters
        ----------
        waveforms : (batch, channel, sample)

        Returns
        -------
        scores : (batch, frame, classes)
        """

        outputs = self.sincnet(waveforms)

        if self.hparams.lstm["monolithic"]:
            outputs, _ = self.lstm(rearrange(outputs, "batch feature frame -> batch frame feature"))
        else:
            outputs = rearrange(outputs, "batch feature frame -> batch frame feature")
            for i, lstm in enumerate(self.lstm):
                outputs, _ = lstm(outputs)
                if i + 1 < self.hparams.lstm["num_layers"]:
                    outputs = self.dropout(outputs)

        if self.hparams.linear["num_layers"] > 0:
            for linear in self.linear:
                outputs = F.leaky_relu(linear(outputs))

        return self.activation(self.classifier(outputs))


class SegmentationModelConfig(PretrainedConfig):
    """Config class associated with SegmentationModel model."""

    model_type = "pyannet"

    def __init__(
        self,
        chunk_duration=10,
        max_speakers_per_frame=2,
        max_speakers_per_chunk=3,
        min_duration=None,
        warm_up=(0.0, 0.0),
        weigh_by_cardinality=False,
        **kwargs,
    ):
        """Init method for the
        Args:
            chunk_duration : float, optional
                    Chunks duration processed by the model. Defaults to 10s.
            max_speakers_per_chunk : int, optional
                Maximum number of speakers per chunk.
            max_speakers_per_frame : int, optional
                Maximum number of (overlapping) speakers per frame.
                Setting this value to 1 or more enables `powerset multi-class` training.
                Default behavior is to use `multi-label` training.
            weigh_by_cardinality: bool, optional
                Weigh each powerset classes by the size of the corresponding speaker set.
                In other words, {0, 1} powerset class weight is 2x bigger than that of {0}
                or {1} powerset classes. Note that empty (non-speech) powerset class is
                assigned the same weight as mono-speaker classes. Defaults to False (i.e. use
                same weight for every class). Has no effect with `multi-label` training.
            min_duration : float, optional
                Sample training chunks duration uniformely between `min_duration`
                and `duration`. Defaults to `duration` (i.e. fixed length chunks).
            warm_up : float or (float, float), optional
                Use that many seconds on the left- and rightmost parts of each chunk
                to warm up the model. While the model does process those left- and right-most
                parts, only the remaining central part of each chunk is used for computing the
                loss during training, and for aggregating scores during inference.
                Defaults to 0. (i.e. no warm-up).
        """
        super().__init__(**kwargs)
        self.chunk_duration = chunk_duration
        self.max_speakers_per_frame = max_speakers_per_frame
        self.max_speakers_per_chunk = max_speakers_per_chunk
        self.min_duration = min_duration
        self.warm_up = warm_up
        self.weigh_by_cardinality = weigh_by_cardinality
        # For now, the model handles only 16000 Hz sampling rate
        self.sample_rate = 16000


class SegmentationModel(PreTrainedModel):
    """
    Wrapper class for the PyanNet segmentation model used in pyannote.
    Inherits from Pretrained model to be compatible with the HF Trainer.
    Can be used to train segmentation models to be used for the "SpeakerDiarisation Task" in pyannote.
    """

    config_class = SegmentationModelConfig

    @property
    def all_tied_weights_keys(self):
        return {}

    def __init__(
        self,
        config=SegmentationModelConfig(),
    ):
        """init method
        Args:
            config (SegmentationModelConfig): instance of SegmentationModelConfig.
        """

        super().__init__(config)

        self.model = PyanNet_nn(sincnet={"stride": 10})

        self.weigh_by_cardinality = config.weigh_by_cardinality
        self.max_speakers_per_frame = config.max_speakers_per_frame
        self.chunk_duration = config.chunk_duration
        self.min_duration = config.min_duration
        self.warm_up = config.warm_up
        self.max_speakers_per_chunk = config.max_speakers_per_chunk

        self.specifications = Specifications(
            problem=Problem.MULTI_LABEL_CLASSIFICATION
            if self.max_speakers_per_frame is None
            else Problem.MONO_LABEL_CLASSIFICATION,
            resolution=Resolution.FRAME,
            duration=self.chunk_duration,
            min_duration=self.min_duration,
            warm_up=self.warm_up,
            classes=[f"speaker#{i+1}" for i in range(self.max_speakers_per_chunk)],
            powerset_max_classes=self.max_speakers_per_frame,
            permutation_invariant=True,
        )
        self.model.specifications = self.specifications
        self.model.build()
        self.setup_loss_func()

    def forward(self, waveforms, labels=None, nb_speakers=None):
        """foward pass of the Pretrained Model.

        Args:
            waveforms (torch.tensor): _description_
            labels (_type_, optional): _description_. Defaults to None.
            nb_speakers (_type_, optional): _description_. Defaults to None.

        Returns:
            _type_: _description_
        """

        prediction = self.model(waveforms.unsqueeze(1))
        batch_size, num_frames, _ = prediction.shape

        if labels is not None:
            weight = torch.ones(batch_size, num_frames, 1, device=waveforms.device)
            warm_up_left = round(self.specifications.warm_up[0] / self.specifications.duration * num_frames)
            weight[:, :warm_up_left] = 0.0
            warm_up_right = round(self.specifications.warm_up[1] / self.specifications.duration * num_frames)
            weight[:, num_frames - warm_up_right :] = 0.0

            if self.specifications.powerset:
                multilabel = self.model.powerset.to_multilabel(prediction)
                permutated_target, _ = permutate(multilabel, labels)

                permutated_target_powerset = self.model.powerset.to_powerset(permutated_target.float())
                loss = self.segmentation_loss(prediction, permutated_target_powerset, weight=weight)

            else:
                permutated_prediction, _ = permutate(labels, prediction)
                loss = self.segmentation_loss(permutated_prediction, labels, weight=weight)

            return {"loss": loss, "logits": prediction}

        return {"logits": prediction}

    def setup_loss_func(self):
        """setup the loss function is self.specifications.powerset is True."""
        if self.specifications.powerset:
            self.model.powerset = Powerset(
                len(self.specifications.classes),
                self.specifications.powerset_max_classes,
            )

    def segmentation_loss(
        self,
        permutated_prediction: torch.Tensor,
        target: torch.Tensor,
        weight: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Permutation-invariant segmentation loss

        Parameters
        ----------
        permutated_prediction : (batch_size, num_frames, num_classes) torch.Tensor
            Permutated speaker activity predictions.
        target : (batch_size, num_frames, num_speakers) torch.Tensor
            Speaker activity.
        weight : (batch_size, num_frames, 1) torch.Tensor, optional
            Frames weight.

        Returns
        -------
        seg_loss : torch.Tensor
            Permutation-invariant segmentation loss
        """

        if self.specifications.powerset:
            # `clamp_min` is needed to set non-speech weight to 1.
            class_weight = torch.clamp_min(self.model.powerset.cardinality, 1.0) if self.weigh_by_cardinality else None
            seg_loss = nll_loss(
                permutated_prediction,
                torch.argmax(target, dim=-1),
                class_weight=class_weight,
                weight=weight,
            )
        else:
            seg_loss = binary_cross_entropy(permutated_prediction, target.float(), weight=weight)

        return seg_loss

    @classmethod
    def from_pyannote_model(cls, pretrained):
        """Copy the weights and architecture of a pre-trained Pyannote model.

        Args:
            pretrained (pyannote.core.Model): pretrained pyannote segmentation model.
        """
        # Initialize model:
        specifications = copy.deepcopy(pretrained.specifications)

        # Copy pretrained model hyperparameters:
        chunk_duration = specifications.duration
        max_speakers_per_frame = specifications.powerset_max_classes
        weigh_by_cardinality = False
        min_duration = specifications.min_duration
        warm_up = specifications.warm_up
        max_speakers_per_chunk = len(specifications.classes)

        config = SegmentationModelConfig(
            chunk_duration=chunk_duration,
            max_speakers_per_frame=max_speakers_per_frame,
            weigh_by_cardinality=weigh_by_cardinality,
            min_duration=min_duration,
            warm_up=warm_up,
            max_speakers_per_chunk=max_speakers_per_chunk,
        )

        model = cls(config)

        # Copy pretrained model weights:
        model.model.hparams = copy.deepcopy(pretrained.hparams)
        model.model.sincnet = copy.deepcopy(pretrained.sincnet)
        model.model.sincnet.load_state_dict(pretrained.sincnet.state_dict())
        model.model.lstm = copy.deepcopy(pretrained.lstm)
        model.model.lstm.load_state_dict(pretrained.lstm.state_dict())
        model.model.linear = copy.deepcopy(pretrained.linear)
        model.model.linear.load_state_dict(pretrained.linear.state_dict())
        model.model.classifier = copy.deepcopy(pretrained.classifier)
        model.model.classifier.load_state_dict(pretrained.classifier.state_dict())
        model.model.activation = copy.deepcopy(pretrained.activation)
        model.model.activation.load_state_dict(pretrained.activation.state_dict())

        return model

    def to_pyannote_model(self):
        """Convert the current model to a pyannote segmentation model for use in pyannote pipelines."""

        seg_model = PyanNet(sincnet={"stride": 10})
        seg_model.hparams.update(self.model.hparams)

        seg_model.sincnet = copy.deepcopy(self.model.sincnet)
        seg_model.sincnet.load_state_dict(self.model.sincnet.state_dict())

        seg_model.lstm = copy.deepcopy(self.model.lstm)
        seg_model.lstm.load_state_dict(self.model.lstm.state_dict())

        seg_model.linear = copy.deepcopy(self.model.linear)
        seg_model.linear.load_state_dict(self.model.linear.state_dict())

        seg_model.classifier = copy.deepcopy(self.model.classifier)
        seg_model.classifier.load_state_dict(self.model.classifier.state_dict())

        seg_model.activation = copy.deepcopy(self.model.activation)
        seg_model.activation.load_state_dict(self.model.activation.state_dict())

        seg_model.specifications = self.specifications

        return seg_model
