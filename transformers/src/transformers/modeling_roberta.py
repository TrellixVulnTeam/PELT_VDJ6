# coding=utf-8
# Copyright 2018 The Google AI Language Team Authors and The HuggingFace Inc. team.
# Copyright (c) 2018, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""PyTorch RoBERTa model. """


import logging

import torch
import torch.nn as nn
from torch.nn import CrossEntropyLoss, MSELoss

from .configuration_roberta import RobertaConfig
from .file_utils import add_start_docstrings, add_start_docstrings_to_callable
from .modeling_bert import BertEmbeddings, BertLayerNorm, BertModel, BertPreTrainedModel, gelu, BertModel
from .modeling_utils import create_position_ids_from_input_ids
import numpy as np
import torch.nn.functional as F


logger = logging.getLogger(__name__)

ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP = {
    "roberta-base": "https://s3.amazonaws.com/models.huggingface.co/bert/roberta-base-pytorch_model.bin",
    "roberta-large": "https://s3.amazonaws.com/models.huggingface.co/bert/roberta-large-pytorch_model.bin",
    "roberta-large-mnli": "https://s3.amazonaws.com/models.huggingface.co/bert/roberta-large-mnli-pytorch_model.bin",
    "distilroberta-base": "https://s3.amazonaws.com/models.huggingface.co/bert/distilroberta-base-pytorch_model.bin",
    "roberta-base-openai-detector": "https://s3.amazonaws.com/models.huggingface.co/bert/roberta-base-openai-detector-pytorch_model.bin",
    "roberta-large-openai-detector": "https://s3.amazonaws.com/models.huggingface.co/bert/roberta-large-openai-detector-pytorch_model.bin",
}


class RobertaEmbeddings(BertEmbeddings):
    """
    Same as BertEmbeddings with a tiny tweak for positional embeddings indexing.
    """

    def __init__(self, config):
        super().__init__(config)
        self.padding_idx = 1
        self.word_embeddings = nn.Embedding(config.vocab_size, config.hidden_size, padding_idx=self.padding_idx)
        self.position_embeddings = nn.Embedding(
            config.max_position_embeddings, config.hidden_size, padding_idx=self.padding_idx
        )

    def forward(self, input_ids=None, token_type_ids=None, position_ids=None, inputs_embeds=None):
        if position_ids is None:
            if input_ids is not None:
                # Create the position ids from the input token ids. Any padded tokens remain padded.
                position_ids = create_position_ids_from_input_ids(input_ids, self.padding_idx).to(input_ids.device)
            else:
                position_ids = self.create_position_ids_from_inputs_embeds(inputs_embeds)

        return super().forward(
            input_ids, token_type_ids=token_type_ids, position_ids=position_ids, inputs_embeds=inputs_embeds
        )

    def create_position_ids_from_inputs_embeds(self, inputs_embeds):
        """ We are provided embeddings directly. We cannot infer which are padded so just generate
        sequential position ids.

        :param torch.Tensor inputs_embeds:
        :return torch.Tensor:
        """
        input_shape = inputs_embeds.size()[:-1]
        sequence_length = input_shape[1]

        position_ids = torch.arange(
            self.padding_idx + 1, sequence_length + self.padding_idx + 1, dtype=torch.long, device=inputs_embeds.device
        )
        return position_ids.unsqueeze(0).expand(input_shape)


ROBERTA_START_DOCSTRING = r"""

    This model is a PyTorch `torch.nn.Module <https://pytorch.org/docs/stable/nn.html#torch.nn.Module>`_ sub-class.
    Use it as a regular PyTorch Module and refer to the PyTorch documentation for all matter related to general
    usage and behavior.

    Parameters:
        config (:class:`~transformers.RobertaConfig`): Model configuration class with all the parameters of the
            model. Initializing with a config file does not load the weights associated with the model, only the configuration.
            Check out the :meth:`~transformers.PreTrainedModel.from_pretrained` method to load the model weights.
"""

ROBERTA_INPUTS_DOCSTRING = r"""
    Args:
        input_ids (:obj:`torch.LongTensor` of shape :obj:`(batch_size, sequence_length)`):
            Indices of input sequence tokens in the vocabulary.

            Indices can be obtained using :class:`transformers.RobertaTokenizer`.
            See :func:`transformers.PreTrainedTokenizer.encode` and
            :func:`transformers.PreTrainedTokenizer.encode_plus` for details.

            `What are input IDs? <../glossary.html#input-ids>`__
        attention_mask (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, sequence_length)`, `optional`, defaults to :obj:`None`):
            Mask to avoid performing attention on padding token indices.
            Mask values selected in ``[0, 1]``:
            ``1`` for tokens that are NOT MASKED, ``0`` for MASKED tokens.

            `What are attention masks? <../glossary.html#attention-mask>`__
        token_type_ids (:obj:`torch.LongTensor` of shape :obj:`(batch_size, sequence_length)`, `optional`, defaults to :obj:`None`):
            Segment token indices to indicate first and second portions of the inputs.
            Indices are selected in ``[0, 1]``: ``0`` corresponds to a `sentence A` token, ``1``
            corresponds to a `sentence B` token

            `What are token type IDs? <../glossary.html#token-type-ids>`_
        position_ids (:obj:`torch.LongTensor` of shape :obj:`(batch_size, sequence_length)`, `optional`, defaults to :obj:`None`):
            Indices of positions of each input sequence tokens in the position embeddings.
            Selected in the range ``[0, config.max_position_embeddings - 1]``.

            `What are position IDs? <../glossary.html#position-ids>`_
        head_mask (:obj:`torch.FloatTensor` of shape :obj:`(num_heads,)` or :obj:`(num_layers, num_heads)`, `optional`, defaults to :obj:`None`):
            Mask to nullify selected heads of the self-attention modules.
            Mask values selected in ``[0, 1]``:
            :obj:`1` indicates the head is **not masked**, :obj:`0` indicates the head is **masked**.
        inputs_embeds (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, sequence_length, hidden_size)`, `optional`, defaults to :obj:`None`):
            Optionally, instead of passing :obj:`input_ids` you can choose to directly pass an embedded representation.
            This is useful if you want more control over how to convert `input_ids` indices into associated vectors
            than the model's internal embedding lookup matrix.
"""


@add_start_docstrings(
    "The bare RoBERTa Model transformer outputting raw hidden-states without any specific head on top.",
    ROBERTA_START_DOCSTRING,
)
class RobertaModel(BertModel):
    """
    This class overrides :class:`~transformers.BertModel`. Please check the
    superclass for the appropriate documentation alongside usage examples.
    """

    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)

        self.embeddings = RobertaEmbeddings(config)
        self.init_weights()

    def get_input_embeddings(self):
        return self.embeddings.word_embeddings

    def set_input_embeddings(self, value):
        self.embeddings.word_embeddings = value


@add_start_docstrings("""RoBERTa Model with a `language modeling` head on top. """, ROBERTA_START_DOCSTRING)
class RobertaForMaskedLM(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)

        self.roberta = RobertaModel(config)
        self.lm_head = RobertaLMHead(config)

        self.init_weights()

    def get_output_embeddings(self):
        return self.lm_head.decoder

    @add_start_docstrings_to_callable(ROBERTA_INPUTS_DOCSTRING)
    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        masked_lm_labels=None,
    ):
        r"""
        masked_lm_labels (:obj:`torch.LongTensor` of shape :obj:`(batch_size, sequence_length)`, `optional`, defaults to :obj:`None`):
            Labels for computing the masked language modeling loss.
            Indices should be in ``[-100, 0, ..., config.vocab_size]`` (see ``input_ids`` docstring)
            Tokens with indices set to ``-100`` are ignored (masked), the loss is only computed for the tokens with labels
            in ``[0, ..., config.vocab_size]``

    Returns:
        :obj:`tuple(torch.FloatTensor)` comprising various elements depending on the configuration (:class:`~transformers.RobertaConfig`) and inputs:
        masked_lm_loss (`optional`, returned when ``masked_lm_labels`` is provided) ``torch.FloatTensor`` of shape ``(1,)``:
            Masked language modeling loss.
        prediction_scores (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, sequence_length, config.vocab_size)`)
            Prediction scores of the language modeling head (scores for each vocabulary token before SoftMax).
        hidden_states (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_hidden_states=True``):
            Tuple of :obj:`torch.FloatTensor` (one for the output of the embeddings + one for the output of each layer)
            of shape :obj:`(batch_size, sequence_length, hidden_size)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        attentions (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_attentions=True``):
            Tuple of :obj:`torch.FloatTensor` (one for each layer) of shape
            :obj:`(batch_size, num_heads, sequence_length, sequence_length)`.

            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention
            heads.

    Examples::

        from transformers import RobertaTokenizer, RobertaForMaskedLM
        import torch

        tokenizer = RobertaTokenizer.from_pretrained('roberta-base')
        model = RobertaForMaskedLM.from_pretrained('roberta-base')
        input_ids = torch.tensor(tokenizer.encode("Hello, my dog is cute", add_special_tokens=True)).unsqueeze(0)  # Batch size 1
        outputs = model(input_ids, masked_lm_labels=input_ids)
        loss, prediction_scores = outputs[:2]

        """
        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )
        sequence_output = outputs[0]
        prediction_scores = self.lm_head(sequence_output)

        outputs = (prediction_scores,) + outputs[2:]  # Add hidden states and attention if they are here

        if masked_lm_labels is not None:
            loss_fct = CrossEntropyLoss(ignore_index=-1)
            masked_lm_loss = loss_fct(prediction_scores.view(-1, self.config.vocab_size), masked_lm_labels.view(-1))
            outputs = (masked_lm_loss,) + outputs

        return outputs  # (masked_lm_loss), prediction_scores, (hidden_states), (attentions)




@add_start_docstrings(
    """RoBERTa Model transformer with a sequence classification/regression head on top (a linear layer
    on top of the pooled output) e.g. for GLUE tasks. """,
    ROBERTA_START_DOCSTRING,
)
class RobertaForSequenceClassification(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)
        self.num_labels = config.num_labels

        self.roberta = RobertaModel(config)
        self.classifier = RobertaClassificationHead(config)

    @add_start_docstrings_to_callable(ROBERTA_INPUTS_DOCSTRING)
    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        labels=None,
    ):
        r"""
        labels (:obj:`torch.LongTensor` of shape :obj:`(batch_size,)`, `optional`, defaults to :obj:`None`):
            Labels for computing the sequence classification/regression loss.
            Indices should be in :obj:`[0, ..., config.num_labels - 1]`.
            If :obj:`config.num_labels == 1` a regression loss is computed (Mean-Square loss),
            If :obj:`config.num_labels > 1` a classification loss is computed (Cross-Entropy).

    Returns:
        :obj:`tuple(torch.FloatTensor)` comprising various elements depending on the configuration (:class:`~transformers.RobertaConfig`) and inputs:
        loss (:obj:`torch.FloatTensor` of shape :obj:`(1,)`, `optional`, returned when :obj:`label` is provided):
            Classification (or regression if config.num_labels==1) loss.
        logits (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, config.num_labels)`):
            Classification (or regression if config.num_labels==1) scores (before SoftMax).
        hidden_states (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_hidden_states=True``):
            Tuple of :obj:`torch.FloatTensor` (one for the output of the embeddings + one for the output of each layer)
            of shape :obj:`(batch_size, sequence_length, hidden_size)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        attentions (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_attentions=True``):
            Tuple of :obj:`torch.FloatTensor` (one for each layer) of shape
            :obj:`(batch_size, num_heads, sequence_length, sequence_length)`.

            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention
            heads.

    Examples::

        from transformers import RobertaTokenizer, RobertaForSequenceClassification
        import torch

        tokenizer = RobertaTokenizer.from_pretrained('roberta-base')
        model = RobertaForSequenceClassification.from_pretrained('roberta-base')
        input_ids = torch.tensor(tokenizer.encode("Hello, my dog is cute", add_special_tokens=True)).unsqueeze(0)  # Batch size 1
        labels = torch.tensor([1]).unsqueeze(0)  # Batch size 1
        outputs = model(input_ids, labels=labels)
        loss, logits = outputs[:2]

        """
        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )
        sequence_output = outputs[0]
        logits = self.classifier(sequence_output)

        outputs = (logits,) + outputs[2:]
        if labels is not None:
            if self.num_labels == 1:
                #  We are doing regression
                loss_fct = MSELoss()
                loss = loss_fct(logits.view(-1), labels.view(-1))
            else:
                loss_fct = CrossEntropyLoss()
                loss = loss_fct(logits.view(-1, self.num_labels), labels.view(-1))
            outputs = (loss,) + outputs

        return outputs  # (loss), logits, (hidden_states), (attentions)





class RobertaForMarkerSequenceClassification(BertPreTrainedModel):
    def __init__(self, config):
        super().__init__(config)
        self.num_labels = config.num_labels

        self.roberta = RobertaModel(config)
        self.classifier = nn.Linear(config.hidden_size*2, config.num_labels)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        labels=None,
        ht_position=None,
    ): 
        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )
        sequence_output = outputs[0]#[:, : input_ids.size(1), :]
        bsz = sequence_output.shape[0]
        h_rep = sequence_output[torch.arange(bsz), ht_position[:,0]]
        t_rep = sequence_output[torch.arange(bsz), ht_position[:,1]]

        ht_rep = torch.cat([h_rep, t_rep], dim=-1)
        ht_rep = self.dropout(ht_rep)

        logits = self.classifier(ht_rep)

        outputs = (logits,) + outputs[2:]
        if labels is not None:
            if self.num_labels == 1:
                #  We are doing regression
                loss_fct = MSELoss()
                loss = loss_fct(logits.view(-1), labels.view(-1))
            else:
                loss_fct = CrossEntropyLoss()
                loss = loss_fct(logits.view(-1, self.num_labels), labels.view(-1))
            outputs = (loss,) + outputs

        return outputs  # (loss), logits, (hidden_states), (attentions)


@add_start_docstrings(
    """Roberta Model with a multiple choice classification head on top (a linear layer on top of
    the pooled output and a softmax) e.g. for RocStories/SWAG tasks. """,
    ROBERTA_START_DOCSTRING,
)
class RobertaForMultipleChoice(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)

        self.roberta = RobertaModel(config)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.classifier = nn.Linear(config.hidden_size, 1)

        self.init_weights()

    @add_start_docstrings_to_callable(ROBERTA_INPUTS_DOCSTRING)
    def forward(
        self,
        input_ids=None,
        token_type_ids=None,
        attention_mask=None,
        labels=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
    ):
        r"""
        labels (:obj:`torch.LongTensor` of shape :obj:`(batch_size,)`, `optional`, defaults to :obj:`None`):
            Labels for computing the multiple choice classification loss.
            Indices should be in ``[0, ..., num_choices]`` where `num_choices` is the size of the second dimension
            of the input tensors. (see `input_ids` above)

    Returns:
        :obj:`tuple(torch.FloatTensor)` comprising various elements depending on the configuration (:class:`~transformers.RobertaConfig`) and inputs:
        loss (:obj:`torch.FloatTensor`` of shape ``(1,)`, `optional`, returned when :obj:`labels` is provided):
            Classification loss.
        classification_scores (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, num_choices)`):
            `num_choices` is the second dimension of the input tensors. (see `input_ids` above).

            Classification scores (before SoftMax).
        hidden_states (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_hidden_states=True``):
            Tuple of :obj:`torch.FloatTensor` (one for the output of the embeddings + one for the output of each layer)
            of shape :obj:`(batch_size, sequence_length, hidden_size)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        attentions (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_attentions=True``):
            Tuple of :obj:`torch.FloatTensor` (one for each layer) of shape
            :obj:`(batch_size, num_heads, sequence_length, sequence_length)`.

            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention
            heads.

    Examples::

        from transformers import RobertaTokenizer, RobertaForMultipleChoice
        import torch

        tokenizer = RobertaTokenizer.from_pretrained('roberta-base')
        model = RobertaForMultipleChoice.from_pretrained('roberta-base')
        choices = ["Hello, my dog is cute", "Hello, my cat is amazing"]
        input_ids = torch.tensor([tokenizer.encode(s, add_special_tokens=True) for s in choices]).unsqueeze(0)  # Batch size 1, 2 choices
        labels = torch.tensor(1).unsqueeze(0)  # Batch size 1
        outputs = model(input_ids, labels=labels)
        loss, classification_scores = outputs[:2]

        """
        num_choices = input_ids.shape[1]

        flat_input_ids = input_ids.view(-1, input_ids.size(-1))
        flat_position_ids = position_ids.view(-1, position_ids.size(-1)) if position_ids is not None else None
        flat_token_type_ids = token_type_ids.view(-1, token_type_ids.size(-1)) if token_type_ids is not None else None
        flat_attention_mask = attention_mask.view(-1, attention_mask.size(-1)) if attention_mask is not None else None
        outputs = self.roberta(
            flat_input_ids,
            position_ids=flat_position_ids,
            token_type_ids=flat_token_type_ids,
            attention_mask=flat_attention_mask,
            head_mask=head_mask,
        )
        pooled_output = outputs[1]

        pooled_output = self.dropout(pooled_output)
        logits = self.classifier(pooled_output)
        reshaped_logits = logits.view(-1, num_choices)

        outputs = (reshaped_logits,) + outputs[2:]  # add hidden states and attention if they are here

        if labels is not None:
            loss_fct = CrossEntropyLoss()
            loss = loss_fct(reshaped_logits, labels)
            outputs = (loss,) + outputs

        return outputs  # (loss), reshaped_logits, (hidden_states), (attentions)


@add_start_docstrings(
    """Roberta Model with a token classification head on top (a linear layer on top of
    the hidden-states output) e.g. for Named-Entity-Recognition (NER) tasks. """,
    ROBERTA_START_DOCSTRING,
)
class RobertaForTokenClassification(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)
        self.num_labels = config.num_labels

        self.roberta = RobertaModel(config)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.classifier = nn.Linear(config.hidden_size, config.num_labels)

        self.init_weights()

    @add_start_docstrings_to_callable(ROBERTA_INPUTS_DOCSTRING)
    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        labels=None,
    ):
        r"""
        labels (:obj:`torch.LongTensor` of shape :obj:`(batch_size, sequence_length)`, `optional`, defaults to :obj:`None`):
            Labels for computing the token classification loss.
            Indices should be in ``[0, ..., config.num_labels - 1]``.

    Returns:
        :obj:`tuple(torch.FloatTensor)` comprising various elements depending on the configuration (:class:`~transformers.RobertaConfig`) and inputs:
        loss (:obj:`torch.FloatTensor` of shape :obj:`(1,)`, `optional`, returned when ``labels`` is provided) :
            Classification loss.
        scores (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, sequence_length, config.num_labels)`)
            Classification scores (before SoftMax).
        hidden_states (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_hidden_states=True``):
            Tuple of :obj:`torch.FloatTensor` (one for the output of the embeddings + one for the output of each layer)
            of shape :obj:`(batch_size, sequence_length, hidden_size)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        attentions (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_attentions=True``):
            Tuple of :obj:`torch.FloatTensor` (one for each layer) of shape
            :obj:`(batch_size, num_heads, sequence_length, sequence_length)`.

            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention
            heads.

    Examples::

        from transformers import RobertaTokenizer, RobertaForTokenClassification
        import torch

        tokenizer = RobertaTokenizer.from_pretrained('roberta-base')
        model = RobertaForTokenClassification.from_pretrained('roberta-base')
        input_ids = torch.tensor(tokenizer.encode("Hello, my dog is cute", add_special_tokens=True)).unsqueeze(0)  # Batch size 1
        labels = torch.tensor([1] * input_ids.size(1)).unsqueeze(0)  # Batch size 1
        outputs = model(input_ids, labels=labels)
        loss, scores = outputs[:2]

        """

        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )

        sequence_output = outputs[0]

        sequence_output = self.dropout(sequence_output)
        logits = self.classifier(sequence_output)

        outputs = (logits,) + outputs[2:]  # add hidden states and attention if they are here
        if labels is not None:
            loss_fct = CrossEntropyLoss()
            # Only keep active parts of the loss
            if attention_mask is not None:
                active_loss = attention_mask.view(-1) == 1
                active_logits = logits.view(-1, self.num_labels)[active_loss]
                active_labels = labels.view(-1)[active_loss]
                loss = loss_fct(active_logits, active_labels)
            else:
                loss = loss_fct(logits.view(-1, self.num_labels), labels.view(-1))
            outputs = (loss,) + outputs

        return outputs  # (loss), scores, (hidden_states), (attentions)


class RobertaClassificationHead(nn.Module):
    """Head for sentence-level classification tasks."""

    def __init__(self, config):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.out_proj = nn.Linear(config.hidden_size, config.num_labels)

    def forward(self, features, **kwargs):
        x = features[:, 0, :]  # take <s> token (equiv. to [CLS])
        x = self.dropout(x)
        x = self.dense(x)
        x = torch.tanh(x)
        x = self.dropout(x)
        x = self.out_proj(x)
        return x


@add_start_docstrings(
    """Roberta Model with a span classification head on top for extractive question-answering tasks like SQuAD (a linear layers on top of
    the hidden-states output to compute `span start logits` and `span end logits`). """,
    ROBERTA_START_DOCSTRING,
)
class RobertaForQuestionAnswering(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)
        self.num_labels = config.num_labels

        self.roberta = RobertaModel(config)
        self.qa_outputs = nn.Linear(config.hidden_size, config.num_labels)

        self.init_weights()

    @add_start_docstrings_to_callable(ROBERTA_INPUTS_DOCSTRING)
    def forward(
        self,
        input_ids,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        start_positions=None,
        end_positions=None,
    ):
        r"""
        start_positions (:obj:`torch.LongTensor` of shape :obj:`(batch_size,)`, `optional`, defaults to :obj:`None`):
            Labels for position (index) of the start of the labelled span for computing the token classification loss.
            Positions are clamped to the length of the sequence (`sequence_length`).
            Position outside of the sequence are not taken into account for computing the loss.
        end_positions (:obj:`torch.LongTensor` of shape :obj:`(batch_size,)`, `optional`, defaults to :obj:`None`):
            Labels for position (index) of the end of the labelled span for computing the token classification loss.
            Positions are clamped to the length of the sequence (`sequence_length`).
            Position outside of the sequence are not taken into account for computing the loss.

    Returns:
        :obj:`tuple(torch.FloatTensor)` comprising various elements depending on the configuration (:class:`~transformers.RobertaConfig`) and inputs:
        loss (:obj:`torch.FloatTensor` of shape :obj:`(1,)`, `optional`, returned when :obj:`labels` is provided):
            Total span extraction loss is the sum of a Cross-Entropy for the start and end positions.
        start_scores (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, sequence_length,)`):
            Span-start scores (before SoftMax).
        end_scores (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, sequence_length,)`):
            Span-end scores (before SoftMax).
        hidden_states (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_hidden_states=True``):
            Tuple of :obj:`torch.FloatTensor` (one for the output of the embeddings + one for the output of each layer)
            of shape :obj:`(batch_size, sequence_length, hidden_size)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        attentions (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_attentions=True``):
            Tuple of :obj:`torch.FloatTensor` (one for each layer) of shape
            :obj:`(batch_size, num_heads, sequence_length, sequence_length)`.

            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention
            heads.

    Examples::

        # The checkpoint roberta-large is not fine-tuned for question answering. Please see the
        # examples/run_squad.py example to see how to fine-tune a model to a question answering task.

        from transformers import RobertaTokenizer, RobertaForQuestionAnswering
        import torch

        tokenizer = RobertaTokenizer.from_pretrained('roberta-base')
        model = RobertaForQuestionAnswering.from_pretrained('roberta-base')

        question, text = "Who was Jim Henson?", "Jim Henson was a nice puppet"
        input_ids = tokenizer.encode(question, text)
        start_scores, end_scores = model(torch.tensor([input_ids]))

        all_tokens = tokenizer.convert_ids_to_tokens(input_ids)
        answer = ' '.join(all_tokens[torch.argmax(start_scores) : torch.argmax(end_scores)+1])

        """

        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )

        sequence_output = outputs[0]

        logits = self.qa_outputs(sequence_output)
        start_logits, end_logits = logits.split(1, dim=-1)
        start_logits = start_logits.squeeze(-1)
        end_logits = end_logits.squeeze(-1)

        outputs = (start_logits, end_logits,) + outputs[2:]
        if start_positions is not None and end_positions is not None:
            # If we are on multi-GPU, split add a dimension
            if len(start_positions.size()) > 1:
                start_positions = start_positions.squeeze(-1)
            if len(end_positions.size()) > 1:
                end_positions = end_positions.squeeze(-1)
            # sometimes the start/end positions are outside our model inputs, we ignore these terms
            ignored_index = start_logits.size(1)
            start_positions.clamp_(0, ignored_index)
            end_positions.clamp_(0, ignored_index)

            loss_fct = CrossEntropyLoss(ignore_index=ignored_index)
            start_loss = loss_fct(start_logits, start_positions)
            end_loss = loss_fct(end_logits, end_positions)
            total_loss = (start_loss + end_loss) / 2
            outputs = (total_loss,) + outputs

        return outputs  # (loss), start_logits, end_logits, (hidden_states), (attentions)

@add_start_docstrings(
    """Roberta Model with a multiple choice classification head on top (a linear layer on top of
    the pooled output and a softmax) e.g. for RocStories/SWAG tasks. """,
    ROBERTA_START_DOCSTRING,
    ROBERTA_INPUTS_DOCSTRING,
)
class RobertaForMultipleChoice(BertPreTrainedModel):
    r"""
    Inputs:
        **input_ids**: ``torch.LongTensor`` of shape ``(batch_size, num_choices, sequence_length)``:
            Indices of input sequence tokens in the vocabulary.
            The second dimension of the input (`num_choices`) indicates the number of choices to score.
            To match pre-training, RoBerta input sequence should be formatted with [CLS] and [SEP] tokens as follows:
            (a) For sequence pairs:
                ``tokens:         [CLS] is this jack ##son ##ville ? [SEP] [SEP] no it is not . [SEP]``
                ``token_type_ids:   0   0  0    0    0     0       0   0   0     1  1  1  1   1   1``
            (b) For single sequences:
                ``tokens:         [CLS] the dog is hairy . [SEP]``
                ``token_type_ids:   0   0   0   0  0     0   0``
            Indices can be obtained using :class:`transformers.BertTokenizer`.
            See :func:`transformers.PreTrainedTokenizer.encode` and
            :func:`transformers.PreTrainedTokenizer.convert_tokens_to_ids` for details.
        **token_type_ids**: (`optional`) ``torch.LongTensor`` of shape ``(batch_size, num_choices, sequence_length)``:
            Segment token indices to indicate first and second portions of the inputs.
            The second dimension of the input (`num_choices`) indicates the number of choices to score.
            Indices are selected in ``[0, 1]``: ``0`` corresponds to a `sentence A` token, ``1``
        **attention_mask**: (`optional`) ``torch.FloatTensor`` of shape ``(batch_size, num_choices, sequence_length)``:
            Mask to avoid performing attention on padding token indices.
            The second dimension of the input (`num_choices`) indicates the number of choices to score.
            Mask values selected in ``[0, 1]``:
            ``1`` for tokens that are NOT MASKED, ``0`` for MASKED tokens.
        **head_mask**: (`optional`) ``torch.FloatTensor`` of shape ``(num_heads,)`` or ``(num_layers, num_heads)``:
            Mask to nullify selected heads of the self-attention modules.
            Mask values selected in ``[0, 1]``:
            ``1`` indicates the head is **not masked**, ``0`` indicates the head is **masked**.
        **inputs_embeds**: (`optional`) ``torch.FloatTensor`` of shape ``(batch_size, sequence_length, embedding_dim)``:
            Optionally, instead of passing ``input_ids`` you can choose to directly pass an embedded representation.
            This is useful if you want more control over how to convert `input_ids` indices into associated vectors
            than the model's internal embedding lookup matrix.
        **labels**: (`optional`) ``torch.LongTensor`` of shape ``(batch_size,)``:
            Labels for computing the multiple choice classification loss.
            Indices should be in ``[0, ..., num_choices]`` where `num_choices` is the size of the second dimension
            of the input tensors. (see `input_ids` above)
    Outputs: `Tuple` comprising various elements depending on the configuration (config) and inputs:
        **loss**: (`optional`, returned when ``labels`` is provided) ``torch.FloatTensor`` of shape ``(1,)``:
            Classification loss.
        **classification_scores**: ``torch.FloatTensor`` of shape ``(batch_size, num_choices)`` where `num_choices` is the size of the second dimension
            of the input tensors. (see `input_ids` above).
            Classification scores (before SoftMax).
        **hidden_states**: (`optional`, returned when ``config.output_hidden_states=True``)
            list of ``torch.FloatTensor`` (one for the output of each layer + the output of the embeddings)
            of shape ``(batch_size, sequence_length, hidden_size)``:
            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        **attentions**: (`optional`, returned when ``config.output_attentions=True``)
            list of ``torch.FloatTensor`` (one for each layer) of shape ``(batch_size, num_heads, sequence_length, sequence_length)``:
            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention heads.
    Examples::
        tokenizer = RobertaTokenizer.from_pretrained('roberta-base')
        model = RobertaForMultipleChoice.from_pretrained('roberta-base')
        choices = ["Hello, my dog is cute", "Hello, my cat is amazing"]
        input_ids = torch.tensor([tokenizer.encode(s, add_special_tokens=True) for s in choices]).unsqueeze(0)  # Batch size 1, 2 choices
        labels = torch.tensor(1).unsqueeze(0)  # Batch size 1
        outputs = model(input_ids, labels=labels)
        loss, classification_scores = outputs[:2]
    """
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super(RobertaForMultipleChoice, self).__init__(config)

        self.roberta = RobertaModel(config)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.classifier = nn.Linear(config.hidden_size, 1)

        self.init_weights()

    def forward(
        self,
        input_ids=None,
        token_type_ids=None,
        attention_mask=None,
        labels=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
    ):
        num_choices = input_ids.shape[1]

        flat_input_ids = input_ids.view(-1, input_ids.size(-1))
        flat_position_ids = position_ids.view(-1, position_ids.size(-1)) if position_ids is not None else None
        flat_token_type_ids = token_type_ids.view(-1, token_type_ids.size(-1)) if token_type_ids is not None else None
        flat_attention_mask = attention_mask.view(-1, attention_mask.size(-1)) if attention_mask is not None else None
        outputs = self.roberta(
            flat_input_ids,
            position_ids=flat_position_ids,
            token_type_ids=flat_token_type_ids,
            attention_mask=flat_attention_mask,
            head_mask=head_mask,
        )
        pooled_output = outputs[1]

        pooled_output = self.dropout(pooled_output)
        logits = self.classifier(pooled_output)
        reshaped_logits = logits.view(-1, num_choices)

        outputs = (reshaped_logits,) + outputs[2:]  # add hidden states and attention if they are here

        if labels is not None:
            loss_fct = CrossEntropyLoss()
            loss = loss_fct(reshaped_logits, labels)
            outputs = (loss,) + outputs

        return outputs  # (loss), reshaped_logits, (hidden_states), (attentions)



class KEHead(nn.Module):
    """Head for sentence-level classification tasks."""

    def __init__(self, config, dim=256):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.out_proj = nn.Linear(config.hidden_size, dim)

    def forward(self, features, **kwargs):
        x = features[:, 0, :]  # take <s> token (equiv. to [CLS])
        x = self.dropout(x)
        x = self.dense(x)
        x = torch.tanh(x)
        x = self.dropout(x)
        x = self.out_proj(x)
        return x



class RobertaForTransE(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)
        self.num_labels = config.num_labels

        self.roberta = RobertaModel(config)

        self.num_ent = 14541 #500000#14541 #8 + 500000#1000000
        self.num_rel = 237 #1127#237  
        self.dim = 1000 #512#1000

        # self.ent_embeddings = nn.Embedding(self.num_ent, self.dim)

        self.ent_embeddings = LargeEmbedding(config.ip_config, 'entity_embed', config.learning_rate, self.num_ent, config.local_rank)
        self.rel_embeddings = nn.Embedding(self.num_rel, self.dim)


        self.p = 1
        self.epsilon = 2.0
        
        self.des_head = KEHead(config, self.dim)
        self.rel_head = KEHead(config, self.dim)


        self._gamma = nn.Parameter(
            torch.Tensor([config.gamma]), 
            requires_grad=False
        )

        self.embedding_range = nn.Parameter(
            torch.Tensor([(self._gamma.item() + self.epsilon) / self.dim]), 
            requires_grad=False
        )

        torch.nn.init.uniform_(
            tensor=self.ent_embeddings.weight.data, 
            a=-self.embedding_range.item(), 
            b=self.embedding_range.item()
        )

        torch.nn.init.uniform_(
            tensor=self.rel_embeddings.weight.data, 
            a=-self.embedding_range.item(), 
            b=self.embedding_range.item()
        )

        self.bert_ceof = 0.05
        # self.bert_ceof = nn.Parameter(
        #     torch.tensor(0.05), 
        #     requires_grad=False
        # )


 
    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        e1_idx=None,
        e2_idx=None,
        neg_e1_idxs=None,
        neg_e2_idxs=None,
        idx=None,
        neg_idxs=None,
        relation_idx=None,
        subsampling_weight=None,
    ):
        h = self.ent_embeddings(e1_idx)
        t = self.ent_embeddings(e2_idx)
        if relation_idx is not None:
            r = self.rel_embeddings(relation_idx)     
            p_score = h + r - t

            if neg_e1_idxs is not None:
                neg_h = self.ent_embeddings(neg_e1_idxs)
                n_score = neg_h + (r - t).unsqueeze(1)
            elif neg_e2_idxs is not None:
                neg_t = self.ent_embeddings(neg_e2_idxs)
                n_score = (h + r).unsqueeze(1) - neg_t
            else:
                assert(False)
        else:
            outputs = self.roberta(
                input_ids,
                attention_mask=attention_mask,
            )
            outputs  = outputs[0]   
            r = self.rel_head(outputs)
            r = self.bert_ceof * r
            p_score = h + r - t
            neg_t = self.ent_embeddings(neg_e2_idxs) 
            n_score = (h + r).unsqueeze(1) - neg_t


            
        negative_score = self._gamma.item() - torch.norm(n_score, p=1, dim=-1)
        if input_ids is None and subsampling_weight is None:
            return (negative_score,)

        positive_score = self._gamma.item() - torch.norm(p_score, p=1, dim=-1)

        negative_score = (F.softmax(negative_score * 1.0, dim = 1).detach() 
                            * F.logsigmoid(-negative_score)).sum(dim = 1)

        positive_score = F.logsigmoid(positive_score)
        
        if subsampling_weight  is not None:
            positive_sample_loss = - (subsampling_weight * positive_score).sum()/subsampling_weight.sum()
            negative_sample_loss = - (subsampling_weight * negative_score).sum()/subsampling_weight.sum()
        else:
            positive_sample_loss = - positive_score.mean()
            negative_sample_loss = - negative_score.mean()
        loss = (positive_sample_loss + negative_sample_loss)/2

        return (loss, )

        # neg_h = self.ent_embeddings(neg_e1_idxs)
        # neg_t = self.ent_embeddings(neg_e2_idxs) 

        # if idx is not None:
        #     outputs = self.roberta(
        #         input_ids,
        #         attention_mask=attention_mask,
        #     )   
        #     outputs  = outputs[0]   
        #     h = self.des_head(outputs)
        #     t = self.ent_embeddings(idx)
        #     # r = torch.ones_like(h)   # TuckER
        #     r = torch.zeros_like(h) #  TransE

        # else:  
        #     # neg_h = self.ent_embeddings(neg_e1_idxs) 

        #     if relation_idx is not None:
        #         h = self.ent_embeddings(e1_idx)
        #         t = self.ent_embeddings(e2_idx)
        #         r = self.rel_embeddings(relation_idx)     

        #     else:
                
        #         outputs = self.roberta(
        #             input_ids,
        #             attention_mask=attention_mask,
        #         )
        #         outputs  = outputs[0]   
        #         h = self.ent_embeddings(e1_idx)
        #         t = self.ent_embeddings(e2_idx)
        #         r = self.rel_head(outputs)

        #     # replace_h_rep = neg_h + (r - t).unsqueeze(1)
        #     # replace_t_rep = (h + r).unsqueeze(1) - neg_t
        #     # nRep = torch.cat([replace_h_rep, replace_t_rep], dim=1)

        # hr = h + r
        # # hr = self.hidden_bn(hr)
        # # hr = self.hidden_dropout(hr)

        # # TransE
        # pRep = hr - t        
        # nRep = (hr).unsqueeze(1) - neg_t

        # nRep_2 = neg_h + (r-t).unsqueeze(1)
        # nRep = torch.cat([nRep, nRep_2], dim=1)


        # pScores = torch.norm(pRep, p=self.p, dim=-1)  # bsz
        # nScores = torch.norm(nRep, p=self.p, dim=-1)  # bsz, neg_num

        # delta = pScores.unsqueeze(-1) - nScores
        # loss = (torch.max(delta, -self.margin)).mean() + self.margin 


        # # pScores = self.margin - torch.norm(pRep, p=self.p, dim=-1)
        # # pLoss = -F.logsigmoid(pScores).mean()
 
        # # nScores = self.margin - torch.norm(nRep, p=self.p, dim=-1)
        # # nLoss = -F.logsigmoid(-nScores).sum() / nScores.shape[0]
        # # loss = pLoss + nLoss
        # return (loss, )   

        # TuckER
        # # x = self.bn0(e1)
        # x = self.input_dropout(h)
        # x = x.view(-1, 1, h.size(1))

        # W_mat = torch.mm(r, self.TuckER_W.view(r.size(1), -1))
        # W_mat = W_mat.view(-1, h.size(1), h.size(1))
        # W_mat = self.hidden_dropout1(W_mat)

        # x = torch.bmm(x, W_mat) 
        # x = x.view(-1, h.size(1))      
        # # x = self.bn1(x)
        # x = self.hidden_dropout2(x)  # bsz, dim


        # neg_pred = torch.matmul(x.unsqueeze(1), neg_t.transpose(1, 2)).squeeze(1)

        # nLoss = -F.logsigmoid(-neg_pred)
        # nLoss = nLoss.sum() / nLoss.shape[0]

        # pos_pred = torch.sum(x*t, dim=-1)

        # pLoss = -F.logsigmoid(pos_pred).mean()

        # loss = pLoss + nLoss
        # return (loss, )   



# class RobertaMergeHead(nn.Module):
#     """Roberta Head for masked language modeling."""

#     def __init__(self, config):
#         super().__init__()
#         self.dense = nn.Linear(config.hidden_size, config.hidden_size)
#         self.layer_norm = BertLayerNorm(config.hidden_size, eps=config.layer_norm_eps)

#         self.decoder = nn.Linear(config.hidden_size, config.hidden_size)


#     def forward(self, features, **kwargs):
#         x = self.dense(features)
#         x = gelu(x)
#         x = self.layer_norm(x)
#         x = self.decoder(x)
#         return x



class RobertaLMHead(nn.Module):
    """Roberta Head for masked language modeling."""

    def __init__(self, config):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.layer_norm = BertLayerNorm(config.hidden_size, eps=config.layer_norm_eps)

        self.decoder = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
        self.bias = nn.Parameter(torch.zeros(config.vocab_size))

        # Need a link between the two variables so that the bias is correctly resized with `resize_token_embeddings`
        self.decoder.bias = self.bias

    def forward(self, features, masked_token_indexes=None):

        x = self.dense(features)
        x = gelu(x)
        x = self.layer_norm(x)

        if masked_token_indexes is not None:
            x = torch.index_select(
                x.view(-1, x.shape[-1]), 0,
                masked_token_indexes)


        # project back to size of vocabulary with bias
        x = self.decoder(x)

        return x


# class RobertaLMHead(nn.Module):
#     """Roberta Head for masked language modeling."""

#     def __init__(self, config):
#         super().__init__()
#         self.dense = nn.Linear(config.hidden_size, config.hidden_size)
#         self.layer_norm = BertLayerNorm(config.hidden_size, eps=config.layer_norm_eps)

#         self.decoder = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
#         self.bias = nn.Parameter(torch.zeros(config.vocab_size))

#         # Need a link between the two variables so that the bias is correctly resized with `resize_token_embeddings`
#         self.decoder.bias = self.bias

#     def forward(self, features, **kwargs):
#         x = self.dense(features)
#         x = gelu(x)
#         x = self.layer_norm(x)

#         # project back to size of vocabulary with bias
#         x = self.decoder(x)

#         return x

class RobertaForMerge(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)

        self.roberta = RobertaModel(config)
        self.bceloss = nn.BCEWithLogitsLoss()
        self.hidden_size = config.hidden_size
        # self.merge_head = RobertaMergeHead(config)
        self.ent_head = nn.Linear(config.hidden_size, config.hidden_size)        
        # self.layer_norm = BertLayerNorm(config.hidden_size, eps=config.layer_norm_eps)
        self.neg_K = config.neg_K
        self.lm_head = RobertaLMHead(config)

        self.init_weights()

        # self.coef = 1 / self.hidden_size
        # torch.nn.init.uniform_(
        #     tensor=self.dense.weight.data, 
        #     a=-self.coef, 
        #     b=self.coef
        # )

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        loss_mask=None,
        is_eval=False,
        lm_labels=None,
    ):
      
        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )
        sequence_output = outputs[0]

        if lm_labels is not None:
            prediction_scores = self.lm_head(sequence_output)

            # loss_fct = CrossEntropyLoss(ignore_index=-1)
            # masked_lm_loss = loss_fct(prediction_scores.view(-1, self.config.vocab_size), lm_labels.view(-1))
            masked_token_indexes = torch.nonzero(
                (lm_labels + 1).view(-1)).view(-1)
            prediction_scores  = self.lm_head(sequence_output, masked_token_indexes)
            target = torch.index_select(lm_labels.view(-1), 0,
                                        masked_token_indexes)

            loss_fct = CrossEntropyLoss(ignore_index=-1)

            masked_lm_loss = loss_fct(prediction_scores.view(-1, self.config.vocab_size), target)


            return (masked_lm_loss, )

        entity_rep = sequence_output[:, -1]
        entity_rep = self.ent_head(entity_rep)
        
        if is_eval:
            return (entity_rep, )

        bsz = sequence_output.shape[0] // (self.neg_K+2)
        # entity_rep = self.layer_norm(entity_rep)
        # entity_rep = self.merge_head(entity_rep)s
        query_rep = entity_rep[:bsz]  # query与query的neg?
        # other_rep = entity_rep[bsz:]  # 无梯度?

        # scores = torch.matmul(query_rep, torch.transpose(other_rep, 1, 0))

        # loss_fct = torch.nn.BCEWithLogitsLoss(reduction='mean')
        # loss = loss_fct(scores.view(-1), loss_mask.view(-1))
  
        # arange_bsz = torch.arange(bsz).to(input_ids)

        # pos_scores =  torch.sigmoid(scores[arange_bsz, arange_bsz])
        # # print (pos_scores)
        # return (loss, pos_scores)



        # pos_rep = entity_rep[bsz:2*bsz]  
        # neg_rep = entity_rep[2*bsz:]

        # pos_scores = torch.sum(query_rep*pos_rep, dim=-1)
        # neg_scores = torch.matmul(query_rep, torch.transpose(neg_rep, 1, 0)) #bsz,bsz

        # p_loss =  -F.logsigmoid(pos_scores).mean()
        # n_loss =  -F.logsigmoid(-neg_scores).mean()
  
        # loss = (p_loss + n_loss)/2

        # return (loss, torch.sigmoid(pos_scores))


        # indices = torch.arange(bsz).to(input_ids)
        pos_rep = entity_rep[bsz:2*bsz]
        neg_rep = entity_rep[2*bsz:].view(bsz, self.neg_K, -1)

        pos_scores = torch.sum(query_rep*pos_rep, dim=-1)
        neg_scores = query_rep.unsqueeze(1) * neg_rep
        neg_scores = torch.sum(neg_scores, dim=-1)



        neg_scores = neg_scores.view(bsz, -1)
        scores = torch.cat([pos_scores.unsqueeze(-1), neg_scores], dim=-1)

        labels = torch.zeros(bsz).to(input_ids)
        loss_fct = CrossEntropyLoss()
        loss = loss_fct(scores, labels)


        # split
        # pos_scores = pos_scores.unsqueeze(1).expand_as(neg_scores)
        # scores = torch.cat([pos_scores.unsqueeze(-1), neg_scores.unsqueeze(-1)], dim=-1)
        # scores = scores.view(-1, 2)

        # labels = torch.zeros(bsz*self.neg_K).to(input_ids)
        # loss_fct = CrossEntropyLoss()
        # loss = loss_fct(scores, labels)


        ## A trick for unused parameters
        tmp = self.lm_head.decoder(sequence_output[0,0])
        loss += 0 * torch.mean(tmp)






        # indices = torch.arange(bsz).to(input_ids)
        # scores = torch.matmul(query_rep, torch.transpose(entity_rep, 1, 0))
        # scores[indices, indices] = -10000.0
        
        # labels = indices + bsz
        # loss_fct = CrossEntropyLoss()
        # # print (bsz)
        # # print (scores[0])
        # # print (scores[0, bsz], scores[0, 2*bsz:2*bsz+4])
        # loss = loss_fct(scores, labels)
        # # print (loss)
        # # exit()
        return (loss, )

        # pos_score = scores[indices, labels]
        # query_l = torch.norm(query_rep, 2, dim=-1)
        # pos_l = torch.norm(entity_rep[bsz:2*bsz], 2, dim=-1)
        
        # pos_cos = pos_score / (query_l * pos_l) 
        # return (loss, pos_cos)



class RobertaForTogether(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)

        self.roberta = RobertaModel(config)
        self.bceloss = nn.BCEWithLogitsLoss()
        self.hidden_size = config.hidden_size
        self.ent_head = nn.Linear(config.hidden_size, 256)#config.hidden_size)        
        self.neg_K = config.neg_K
        self.lm_head = RobertaLMHead(config)
        self.in_batch = True
        self.is_detach = config.detach
        self._gamma = 5
        self.p = 1

        self.init_weights()
 
    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        loss_mask=None,
        is_eval=False,
        lm_labels=None,
        cur_page_ids=None,
        neg_page_ids=None,
    ):
        bsz = lm_labels.shape[0]  

        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )
        sequence_output = outputs[0]

        mlm_rep = sequence_output[:bsz]

        entity_rep = sequence_output[bsz:, -1]
        entity_rep = self.ent_head(entity_rep)
        # entity_l = torch.norm(entity_rep, p=2, dim=-1)
        # entity_rep = entity_rep / entity_l.unsqueeze(-1)
        
        if is_eval:
            return (entity_rep, )
        query_rep = entity_rep[:bsz]   
        pos_rep = entity_rep[bsz:2*bsz].detach()
        neg_rep = entity_rep[2*bsz:].detach()

        if self.is_detach:
            pos_rep = pos_rep.detach()
            neg_rep = neg_rep.detach()


        if self.p is None:
            pos_scores = torch.sum(query_rep*pos_rep, dim=-1)

            if self.in_batch:
                # in-batch negative
                cur_page_ids = cur_page_ids.unsqueeze(-1)
                neg_page_ids = neg_page_ids.view(-1).unsqueeze(0)
                loss_mask = (cur_page_ids==neg_page_ids).to(neg_rep) # (bsz, (bsz*neg_K))
                neg_scores = torch.matmul(query_rep,  torch.transpose(neg_rep, 0, 1))
                neg_scores = neg_scores - 10000.0*loss_mask
            else:
                # normal negative
                neg_rep = neg_rep.view(bsz, self.neg_K, -1) 
                neg_scores = query_rep.unsqueeze(1) * neg_rep
                neg_scores = torch.sum(neg_scores, dim=-1)
                neg_scores = neg_scores.view(bsz, -1)
            scores = torch.cat([pos_scores.unsqueeze(-1), neg_scores], dim=-1)
            labels = torch.zeros(bsz).to(input_ids)

            loss_fct = CrossEntropyLoss()
            ent_loss = loss_fct(scores, labels)

        else:

            if self.in_batch:
                cur_page_ids = cur_page_ids.unsqueeze(-1)
                neg_page_ids = neg_page_ids.view(-1).unsqueeze(0)
                loss_mask = (cur_page_ids!=neg_page_ids).to(neg_rep) # (bsz, (bsz*neg_K))
                neg_rep = neg_rep.view(bsz*self.neg_K, -1).unsqueeze(0)
                neg_scores = query_rep.unsqueeze(1)-neg_rep
                
                
                negative_score = self._gamma - torch.norm(neg_scores, p=self.p, dim=-1)

                valid = loss_mask.sum()
                negative_score = F.logsigmoid(-negative_score) * loss_mask
                negative_sample_loss = - (negative_score/valid).sum()

            else:
                neg_rep = neg_rep.view(bsz, self.neg_K, -1) 
                neg_scores = query_rep.unsqueeze(1) - neg_rep
                
                negative_score = self._gamma - torch.norm(neg_scores, p=self.p, dim=-1)
                negative_score = F.logsigmoid(-negative_score)
                negative_sample_loss = - negative_score.mean()

            pos_scores = query_rep-pos_rep

            positive_score = self._gamma - torch.norm(pos_scores, p=self.p, dim=-1)
            positive_score = F.logsigmoid(positive_score)
            
            positive_sample_loss = - positive_score.mean()
            ent_loss = (positive_sample_loss + negative_sample_loss)/2

        # merge
        # scores = torch.cat([pos_scores.unsqueeze(-1), neg_scores], dim=-1)
        # labels = torch.zeros(bsz).to(input_ids)

        # loss_fct = CrossEntropyLoss()
        # ent_loss = loss_fct(scores, labels)

        # split
        # pos_scores = pos_scores.unsqueeze(1).expand_as(neg_scores)
        # scores = torch.cat([pos_scores.unsqueeze(-1), neg_scores.unsqueeze(-1)], dim=-1)
        # scores = scores.view(-1, 2)

        # labels = torch.zeros(scores.shape[0]).to(input_ids)
        # loss_fct = CrossEntropyLoss()
        # ent_loss = loss_fct(scores, labels)
        
        # margin

        # negative_score = (F.softmax(negative_score * 1.0, dim = 1).detach() 
        #                     * F.logsigmoid(-negative_score)).sum(dim = 1)
        


        prediction_scores = self.lm_head(mlm_rep)

        masked_token_indexes = torch.nonzero( (lm_labels + 1).view(-1) ).view(-1)
        prediction_scores  = self.lm_head(sequence_output, masked_token_indexes)
        target = torch.index_select(lm_labels.view(-1), 0,  masked_token_indexes)

        loss_fct = CrossEntropyLoss(ignore_index=-1)

        masked_lm_loss = loss_fct(prediction_scores.view(-1, self.config.vocab_size), target)

        loss = ent_loss + masked_lm_loss

        return (loss, ent_loss, masked_lm_loss)


class RobertaForEmbed(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)

        self.roberta = RobertaModel(config)
        # self.bceloss = nn.BCEWithLogitsLoss()
        self.hidden_size = config.hidden_size
        self.ent_head = nn.Linear(config.hidden_size, 256)        
        # self.neg_K = config.neg_K
        # self.lm_head = RobertaLMHead(config)

        self.init_weights()
 
    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        loss_mask=None,
        is_eval=False,
        lm_labels=None,
    ):


        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )
        sequence_output = outputs[0]

        entity_rep = sequence_output[:, -1]
        entity_rep = self.ent_head(entity_rep)
        # print (input_ids[0])

        # print (entity_rep[0, :20])
        # exit()

        return (entity_rep, )






class RobertaForFix(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)

        self.roberta = RobertaModel(config)
        # self.bceloss = nn.BCEWithLogitsLoss()
        self.hidden_size = config.hidden_size
        # self.ent_head = nn.Linear(config.hidden_size, 256)#config.hidden_size)        
        self.neg_K = config.neg_K

        self.in_batch = True
        # self.is_detach = config.detach
        # self._gamma = nn.Parameter(
        #     torch.Tensor([config.gamma]), 
        #     requires_grad=False
        # )
        self.lm_head = RobertaSimpleLMHead(config)

        self.init_weights()
 
    def get_output_embeddings(self):
        return self.lm_head.decoder

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        loss_mask=None,
        is_eval=False,
        lm_labels=None,
        cur_page_ids=None,
        neg_page_ids=None,
    ):
        bsz = input_ids.shape[0]  // (self.neg_K+2)

        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )
        sequence_output = outputs[0]

        entity_rep = sequence_output[:, -1]
        entity_rep = self.lm_head(entity_rep)


        query_rep = entity_rep[:bsz]   
        pos_rep = entity_rep[bsz:2*bsz]
        neg_rep = entity_rep[2*bsz:]

        # if self.is_detach:
        #     pos_rep = pos_rep.detach()
        #     neg_rep = neg_rep.detach()

        pos_scores = torch.sum(query_rep*pos_rep, dim=-1)

        if self.in_batch:
            # in-batch negative
            cur_page_ids = cur_page_ids.unsqueeze(-1)
            neg_page_ids = neg_page_ids.view(-1).unsqueeze(0)
            loss_mask = (cur_page_ids==neg_page_ids).to(neg_rep) # (bsz, (bsz*neg_K))
            neg_scores = torch.matmul(query_rep,  torch.transpose(neg_rep, 0, 1))
            neg_scores = neg_scores - 10000.0*loss_mask
        else:
            # normal negative
            neg_rep = neg_rep.view(bsz, self.neg_K, -1) 
            neg_scores = query_rep.unsqueeze(1) * neg_rep
            neg_scores = torch.sum(neg_scores, dim=-1)
            neg_scores = neg_scores.view(bsz, -1)

        # merge
        scores = torch.cat([pos_scores.unsqueeze(-1), neg_scores], dim=-1)
        labels = torch.zeros(bsz).to(input_ids)

        loss_fct = CrossEntropyLoss()
        ent_loss = loss_fct(scores, labels)

        # split
        # pos_scores = pos_scores.unsqueeze(1).expand_as(neg_scores)
        # scores = torch.cat([pos_scores.unsqueeze(-1), neg_scores.unsqueeze(-1)], dim=-1)
        # scores = scores.view(-1, 2)

        # labels = torch.zeros(scores.shape[0]).to(input_ids)
        # loss_fct = CrossEntropyLoss()
        # ent_loss = loss_fct(scores, labels)


        return (ent_loss, )




class RobertaSimpleLMHead(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.layer_norm = BertLayerNorm(config.hidden_size, eps=config.layer_norm_eps)


        self.decoder = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
        self.bias = nn.Parameter(torch.zeros(config.vocab_size))

        # Need a link between the two variables so that the bias is correctly resized with `resize_token_embeddings`
        self.decoder.bias = self.bias

    def forward(self, features, masked_token_indexes=None):
        x = self.dense(features)
        x = gelu(x)
        x = self.layer_norm(x)
        return x

class RobertaForRep(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)

        self.roberta = RobertaModel(config)
        self.lm_head = RobertaSimpleLMHead(config)

        self.init_weights()


    def get_output_embeddings(self):
        return self.lm_head.decoder

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        mask_position=None,
    ): 
        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )
        sequence_output = outputs[0]
        if mask_position is not None:
            idx = torch.arange(sequence_output.shape[0]).to(mask_position)
            sequence_output = sequence_output[idx, mask_position]
        rep = self.lm_head(sequence_output)

        outputs = (rep,) + outputs[2:]  # Add hidden states and attention if they are here

        return outputs  # (masked_lm_loss), prediction_scores, (hidden_states), (attentions)

class Similarity(nn.Module):
    """
    Dot product or cosine similarity
    """

    def __init__(self, temp):
        super().__init__()
        self.temp = temp
        self.cos = nn.CosineSimilarity(dim=-1)

    def forward(self, x, y):
        return self.cos(x, y) / self.temp


class RobertaForPair(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)

        self.roberta = RobertaModel(config)
        self.lm_head = RobertaSimpleLMHead(config)
        self.sim = Similarity(temp=config.temperature)
        self.num_m_prompt = config.num_m_prompt
        self.calibration = config.calibration
        self.init_weights()

    def get_output_embeddings(self):
        return self.lm_head.decoder

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        mask_position=None,
        mlm_labels=None,
    ): 
        bsz, K, seq_len = input_ids.size()
        input_ids = input_ids.view(bsz*K, seq_len)
        attention_mask = attention_mask.view(bsz*K, seq_len)
        mask_position = mask_position.view(bsz*K)

        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )
        total_sequence_output = outputs[0]
        idx = torch.arange(total_sequence_output.shape[0]).to(mask_position)
        sequence_output = total_sequence_output[idx, mask_position] # bsz*K, dim
        dim = sequence_output.size(-1)

        sequence_output_reshape = sequence_output.view(bsz, K, -1)
        z1 = sequence_output_reshape[:, 0, :]  # bsz, dim
        z3 = sequence_output_reshape[:, 1:, :].contiguous().view(-1, dim)

        cos_sim = self.sim(z1.unsqueeze(1), z3.unsqueeze(0))  # bsz, bsz*(K-1)

        labels = torch.arange(bsz).to(mask_position) * (K-1)

        loss_fct = nn.CrossEntropyLoss(ignore_index=-1)
        loss = entity_loss = loss_fct(cos_sim, labels)

        if mlm_labels is not None:
            mlm_prediction = self.lm_head.decoder(sequence_output)
            with torch.no_grad():
                ori_sequence_output = total_sequence_output[idx, mask_position-self.num_m_prompt]
                ori_mlm_prediction = self.lm_head.decoder(ori_sequence_output)

            if not self.calibration:
                mlm_loss = loss_fct(mlm_prediction, mlm_labels.view(-1))
                loss += mlm_loss
                with torch.no_grad():
                    ori_mlm_loss = loss_fct(ori_mlm_prediction, mlm_labels.view(-1))
            else:
                loss_fct = nn.CrossEntropyLoss(ignore_index=-1, reduce=False)
                mlm_loss = loss_fct(mlm_prediction, mlm_labels.view(-1))
                with torch.no_grad():
                    ori_mlm_loss = loss_fct(ori_mlm_prediction, mlm_labels.view(-1)).detach()

                tot = torch.sum(mlm_labels>=0)
                record_mlm_loss = mlm_loss.clone().detach()
                delta = record_mlm_loss - ori_mlm_loss

                mlm_loss[delta < 0] = 0
                mlm_loss = (mlm_loss / tot).sum()
                ori_mlm_loss = (ori_mlm_loss / tot).sum()
                loss += mlm_loss

                mlm_loss = (record_mlm_loss / tot).sum()
        else:
            mlm_loss = 0
            ori_mlm_loss = 0
        # sequence_output = sequence_output.view(bsz, K, -1)

        # e1 = sequence_output[:, 0, :]
        # e_other = sequence_output[:, 1:, :]

        # scores = torch.sum(e1.unsqueeze(1) * e_other, dim=-1) # bsz, K+1

        # pos_scores = scores[:, 0]
        # neg_scores = scores[:, 1:]

        # pos_loss = - torch.sum(torch.nn.functional.sigmoid(pos_scores))
        # neg_loss = - torch.sum(torch.nn.functional.sigmoid(-neg_scores))
        # loss = pos_loss + neg_loss
        # loss = loss / bsz

        outputs = (loss, entity_loss, mlm_loss, ori_mlm_loss) + outputs[2:]  # Add hidden states and attention if they are here

        return outputs  # (masked_lm_loss), prediction_scores, (hidden_states), (attentions)



class RobertaForTriviaQuestionAnswering(BertPreTrainedModel):
    config_class = RobertaConfig
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)
        self.num_labels = config.num_labels

        self.roberta = RobertaModel(config)
        self.qa_outputs = nn.Linear(config.hidden_size, config.num_labels)

        self.init_weights()

    def or_softmax_cross_entropy_loss_one_doc(self, logits, target, ignore_index=-1, dim=-1):
        """loss function suggested in section 2.2 here https://arxiv.org/pdf/1710.10723.pdf"""
        # assert logits.ndim == 2
        # assert target.ndim == 2
        # assert logits.size(0) == target.size(0)

        # with regular CrossEntropyLoss, the numerator is only one of the logits specified by the target
        # here, the numerator is the sum of a few potential targets, where some of them is the correct answer
        bsz = logits.shape[0]

        # compute a target mask
        target_mask = target == ignore_index
        # replaces ignore_index with 0, so `gather` will select logit at index 0 for the msked targets
        masked_target = target * (1 - target_mask.long())
        # gather logits
        gathered_logits = logits.gather(dim=dim, index=masked_target)
        # Apply the mask to gathered_logits. Use a mask of -inf because exp(-inf) = 0
        gathered_logits[target_mask] = -10000.0#float('-inf')

        # each batch is one example
        gathered_logits = gathered_logits.view(bsz, -1)
        logits = logits.view(bsz, -1)

        # numerator = log(sum(exp(gathered logits)))
        log_score = torch.logsumexp(gathered_logits, dim=dim, keepdim=False)
        # denominator = log(sum(exp(logits)))
        log_norm = torch.logsumexp(logits, dim=dim, keepdim=False)

        # compute the loss
        loss = -(log_score - log_norm)

        # some of the examples might have a loss of `inf` when `target` is all `ignore_index`.
        # remove those from the loss before computing the sum. Use sum instead of mean because
        # it is easier to compute
        # return loss[~torch.isinf(loss)].sum()
        return loss.mean()#loss.sum() / len(loss)    

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        start_positions=None,
        end_positions=None,
        answer_masks=None,
    ):
        bsz = input_ids.shape[0]
        max_segment = input_ids.shape[1]

        input_ids = input_ids.view(-1, input_ids.size(-1))
        attention_mask = attention_mask.view(-1, attention_mask.size(-1)) if attention_mask is not None else None
        token_type_ids = token_type_ids.view(-1, token_type_ids.size(-1)) if token_type_ids is not None else None
        position_ids = position_ids.view(-1, position_ids.size(-1)) if position_ids is not None else None

        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,

        )

        sequence_output = outputs[0]

        logits = self.qa_outputs(sequence_output)
        start_logits, end_logits = logits.split(1, dim=-1)
        start_logits = start_logits.squeeze(-1)
        end_logits = end_logits.squeeze(-1)

        start_logits = start_logits.view(bsz, max_segment, -1) # (bsz, max_segment, seq_length)
        end_logits = end_logits.view(bsz, max_segment, -1) # (bsz, max_segment, seq_length)


        outputs = (start_logits, end_logits,) + outputs[2:]

        if start_positions is not None and end_positions is not None:

            start_loss = self.or_softmax_cross_entropy_loss_one_doc(start_logits, start_positions, ignore_index=-1)
            end_loss = self.or_softmax_cross_entropy_loss_one_doc(end_logits, end_positions, ignore_index=-1)

            total_loss = (start_loss + end_loss) / 2

            outputs = (total_loss,) + outputs

        return outputs  



class RobertaForQuestionAnsweringHotpotSeg(BertPreTrainedModel):
    config_class = RobertaConfig
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)
        self.num_labels = config.num_labels

        self.roberta = RobertaModel(config)
        self.qa_outputs = nn.Linear(config.hidden_size, config.num_labels)
        self.sent_linear = nn.Linear(config.hidden_size*2, config.hidden_size) 
        self.sent_classifier = nn.Linear(config.hidden_size, 2) 

        self.init_weights()

    def or_softmax_cross_entropy_loss_one_doc(self, logits, target, ignore_index=-1, dim=-1):
        """loss function suggested in section 2.2 here https://arxiv.org/pdf/1710.10723.pdf"""
        # assert logits.ndim == 2
        # assert target.ndim == 2
        # assert logits.size(0) == target.size(0)

        # with regular CrossEntropyLoss, the numerator is only one of the logits specified by the target
        # here, the numerator is the sum of a few potential targets, where some of them is the correct answer
        bsz = logits.shape[0]

        # compute a target mask
        target_mask = target == ignore_index
        # replaces ignore_index with 0, so `gather` will select logit at index 0 for the msked targets
        masked_target = target * (1 - target_mask.long())
        # gather logits
        gathered_logits = logits.gather(dim=dim, index=masked_target)
        # Apply the mask to gathered_logits. Use a mask of -inf because exp(-inf) = 0
        gathered_logits[target_mask] = -10000.0#float('-inf')

        # each batch is one example
        gathered_logits = gathered_logits.view(bsz, -1)
        logits = logits.view(bsz, -1)

        # numerator = log(sum(exp(gathered logits)))
        log_score = torch.logsumexp(gathered_logits, dim=dim, keepdim=False)
        # denominator = log(sum(exp(logits)))
        log_norm = torch.logsumexp(logits, dim=dim, keepdim=False)

        # compute the loss
        loss = -(log_score - log_norm)

        # some of the examples might have a loss of `inf` when `target` is all `ignore_index`.
        # remove those from the loss before computing the sum. Use sum instead of mean because
        # it is easier to compute
        # return loss[~torch.isinf(loss)].sum()
        return loss.mean()#loss.sum() / len(loss)    

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        start_positions=None,
        end_positions=None,
        answer_masks=None,
        sent_start_mapping=None,
        sent_end_mapping=None,
        sent_labels=None,
    ):
        bsz = input_ids.shape[0]
        max_segment = input_ids.shape[1]

        input_ids = input_ids.view(-1, input_ids.size(-1))
        attention_mask = attention_mask.view(-1, attention_mask.size(-1)) if attention_mask is not None else None
        token_type_ids = token_type_ids.view(-1, token_type_ids.size(-1)) if token_type_ids is not None else None
        position_ids = position_ids.view(-1, position_ids.size(-1)) if position_ids is not None else None
        sent_start_mapping = sent_start_mapping.view(bsz*max_segment, -1, sent_start_mapping.size(-1)) if sent_start_mapping is not None else None
        sent_end_mapping = sent_end_mapping.view(bsz*max_segment, -1, sent_end_mapping.size(-1)) if sent_end_mapping is not None else None

        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,

        )

        sequence_output = outputs[0]

        logits = self.qa_outputs(sequence_output)
        start_logits, end_logits = logits.split(1, dim=-1)
        start_logits = start_logits.squeeze(-1)
        end_logits = end_logits.squeeze(-1)

        start_logits = start_logits.view(bsz, max_segment, -1) # (bsz, max_segment, seq_length)
        end_logits = end_logits.view(bsz, max_segment, -1) # (bsz, max_segment, seq_length)

        if sent_start_mapping is not None:
            start_rep = torch.matmul(sent_start_mapping, sequence_output)
            end_rep = torch.matmul(sent_end_mapping, sequence_output)
            sent_rep = torch.cat([start_rep, end_rep], dim=-1)

            sent_logits = gelu(self.sent_linear(sent_rep))
            sent_logits = self.sent_classifier(sent_logits).squeeze(-1)
        else:
            sent_logits = None
        outputs = (start_logits, end_logits, sent_logits) + outputs[2:]

        if start_positions is not None and end_positions is not None:

            start_loss = self.or_softmax_cross_entropy_loss_one_doc(start_logits, start_positions, ignore_index=-1)
            end_loss = self.or_softmax_cross_entropy_loss_one_doc(end_logits, end_positions, ignore_index=-1)
            loss_fct = CrossEntropyLoss(ignore_index=-1)

            sent_loss = loss_fct(sent_logits.view(-1, 2), sent_labels.view(-1))

            total_loss = (start_loss + end_loss) / 2 + sent_loss

            outputs = (total_loss,) + outputs

        return outputs  



class RobertaForWikihopMulti(BertPreTrainedModel):
    config_class = RobertaConfig
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)
        self.num_labels = config.num_labels

        self.roberta = RobertaModel(config)

        self.qa_outputs = nn.Linear(config.hidden_size, 1)

        self.init_weights()


    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        cand_positions=None,
        answer_index=None,
        instance_mask=None,

    ):
        bsz = input_ids.shape[0]
        max_segment = input_ids.shape[1]

        input_ids = input_ids.view(-1, input_ids.size(-1))
        attention_mask = attention_mask.view(-1, attention_mask.size(-1)) if attention_mask is not None else None
        token_type_ids = token_type_ids.view(-1, token_type_ids.size(-1)) if token_type_ids is not None else None
        position_ids = position_ids.view(-1, position_ids.size(-1)) if position_ids is not None else None


        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )

        sequence_output = outputs[0]

        logits = self.qa_outputs(sequence_output).squeeze(-1)  # (bsz*max_segment, seq_length)
        logits = logits.view(bsz, max_segment, -1) # (bsz, max_segment, seq_length)

        ignore_index = -1
        target = cand_positions  # (bsz, 79)
        target = target.unsqueeze(1).expand(-1, max_segment, -1)  # (bsz, max_segment, 79)
        target_mask = (target == ignore_index)
        masked_target = target * (1 - target_mask.long())
        gathered_logits = logits.gather(dim=-1, index=masked_target)  
        gathered_logits[target_mask] = -10000.0  # (bsz, max_segment, 79)
        instance_mask = instance_mask.to(gathered_logits)
        gathered_logits = torch.sum(gathered_logits * instance_mask.unsqueeze(-1), dim=1)
        gathered_logits = gathered_logits / torch.sum(instance_mask, dim=1).unsqueeze(-1)


        outputs = (gathered_logits,) + outputs[2:]

        if answer_index is not None:

            loss_fct = CrossEntropyLoss()
            loss = loss_fct(gathered_logits, answer_index)
        
            outputs = (loss,) + outputs

        return outputs  



class RobertaForLMQA(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)

        self.roberta = RobertaModel(config)
        self.lm_head = RobertaSimpleLMHead(config)
        self.sim = Similarity(temp=config.temperature)

        self.init_weights()


    def get_output_embeddings(self):
        return self.lm_head.decoder

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        # token_type_ids=None,
        # position_ids=None,
        # head_mask=None,
        # inputs_embeds=None,
        q_mask_position=None,  # bsz, num_cands
        cand_mask_position=None,
        answer_index=None,
    ): 
        bsz, num_cands, seq_len = input_ids.size()
        input_ids = input_ids.view(bsz*num_cands, seq_len)
        attention_mask = attention_mask.view(bsz*num_cands, seq_len)

        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            # token_type_ids=token_type_ids,
            # position_ids=position_ids,
            # head_mask=head_mask,
            # inputs_embeds=inputs_embeds,
        )

        sequence_output = outputs[0]
        sequence_output = self.lm_head(sequence_output)

        idx = torch.arange(bsz).to(input_ids)
        query_state = sequence_output[idx, q_mask_position]
        query_state = query_state.unsqueeze(1)

        idx = torch.arange(bsz*num_cands).to(input_ids)
        cand_state = sequence_output[idx, cand_mask_position.view(-1)]
        cand_state = cand_state.view(bsz, num_cands, -1)
        # logits = torch.sum(query_state*cand_state, dim=-1)  # bsz, num_cands
        logits = self.sim(query_state, cand_state)
        novalid_mask = (cand_mask_position==0).to(sequence_output)
        logits = logits - 10000.0 * novalid_mask

        outputs = (logits,) + outputs[2:]

        if answer_index is not None:

            loss_fct = CrossEntropyLoss()
            loss = loss_fct(logits, answer_index)
        
            outputs = (loss,) + outputs

        return outputs




class RobertaLMLinear(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.layer_norm = BertLayerNorm(config.hidden_size, eps=config.layer_norm_eps)

    def forward(self, features, masked_token_indexes=None):
        x = self.dense(features)
        x = gelu(x)
        x = self.layer_norm(x)
        return x


# class RobertaForLMMarkerQA(BertPreTrainedModel):
#     config_class = RobertaConfig
#     pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
#     base_model_prefix = "roberta"

#     def __init__(self, config):
#         super().__init__(config)
#         self.num_labels = config.num_labels
#         self.max_seq_length = config.max_seq_length
#         self.roberta = RobertaModel(config)
#         self.lm_head = RobertaLMLinear(config)
#         # self.no_answer_word_embeddings = nn.Paramerters(config.hidden_size)
#         self.sim = Similarity(temp=config.temperature)

#         self.init_weights()

#     def forward(
#         self,
#         input_ids=None,
#         attention_mask=None,
#         token_type_ids=None,
#         position_ids=None,
#         head_mask=None,
#         inputs_embeds=None,
#         q_mask_position=None,
#         answer_positions=None,
#     ):

#         outputs = self.roberta(
#             input_ids,
#             attention_mask=attention_mask,
#             token_type_ids=token_type_ids,
#             position_ids=position_ids,
#             head_mask=head_mask,
#             inputs_embeds=inputs_embeds,
#         )
#         sequence_output = outputs[0]
#         bsz, tot_seq_len = input_ids.shape
#         seq_len = self.max_seq_length
#         ent_len = (tot_seq_len - seq_len) // 2

#         idx = torch.arange(bsz).to(input_ids)
#         query_state = sequence_output[idx, q_mask_position]
#         query_state = query_state.unsqueeze(1)

#         answer_states = sequence_output[:, seq_len:seq_len+ent_len]

#         answer_states = self.lm_head(answer_states)  # bsz, num_pair, dim
#         query_state = self.lm_head(query_state)

#         # no_answer_word_embeddings = self.no_answer_word_embeddings.unsqueeze(0).repeat(bsz, -1)
#         # answer_states = torch.cat([answer_states, no_answer_word_embeddings.unsqueeze(1)], dim=1)
#         # logits = query_state * answer_states
#         # logits = torch.sum(logits, dim=-1)



#         # padding = torch.zeros((bsz, 1)).to(query_state)
#         logits = self.sim(query_state, answer_states) # bsz, num_K
#         # logits = torch.cat([logits, padding], dim=-1)

#         outputs = (logits,) + outputs[2:]
#         if answer_positions is not None:
#             loss_fct = CrossEntropyLoss(ignore_index=-1, reduction='none')

#             losses = [loss_fct(logits, _answer_position) for _answer_position in torch.unbind(answer_positions, dim=1)]

#             loss = sum(losses)

#             # ge = torch.sum(answer_positions>=0, dim=-1).to(start_loss)
#             # loss = loss / ge

#             loss = torch.mean(loss)

#             outputs = (loss,) + outputs


#         return outputs  





class RobertaForLMMarkerQA(BertPreTrainedModel):
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super().__init__(config)
        self.num_labels = config.num_labels
        self.type = config.type
        self.max_seq_length = config.max_seq_length
        self.roberta = RobertaModel(config)
        self.lm_head = RobertaLMLinear(config)
        # self.no_answer_word_embeddings = nn.Paramerters(config.hidden_size)
        self.sim = Similarity(temp=config.temperature)

        self.init_weights()

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        q_mask_position=None,
        answer_positions=None,
    ):

        outputs = self.roberta(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )
        sequence_output = outputs[0]
        bsz, tot_seq_len = input_ids.shape
        seq_len = self.max_seq_length
        ent_len = (tot_seq_len - seq_len) // 2

        idx = torch.arange(bsz).to(input_ids)
        query_state = sequence_output[idx, q_mask_position]
        query_state = query_state.unsqueeze(1)

        answer_states = sequence_output[:, seq_len:seq_len+ent_len]


        # no_answer_word_embeddings = self.no_answer_word_embeddings.unsqueeze(0).repeat(bsz, -1)
        # answer_states = torch.cat([answer_states, no_answer_word_embeddings.unsqueeze(1)], dim=1)
        if self.type==0:
            logits = query_state * answer_states
            logits = torch.sum(logits, dim=-1)
        else:
            answer_states = self.lm_head(answer_states)  # bsz, num_pair, dim
            query_state = self.lm_head(query_state)
            if self.type==1:
                logits = query_state * answer_states
                logits = torch.sum(logits, dim=-1)
            else:
                logits = self.sim(query_state, answer_states) # bsz, num_K

        # padding = torch.zeros((bsz, 1)).to(query_state)
        # logits = torch.cat([logits, padding], dim=-1)

        outputs = (logits,) + outputs[2:]
        if answer_positions is not None:
            loss_fct = CrossEntropyLoss(ignore_index=-1, reduction='none')

            losses = [loss_fct(logits, _answer_position) for _answer_position in torch.unbind(answer_positions, dim=1)]

            loss = sum(losses)

            # ge = torch.sum(answer_positions>=0, dim=-1).to(start_loss)
            # loss = loss / ge

            loss = torch.mean(loss)

            outputs = (loss,) + outputs


        return outputs  
