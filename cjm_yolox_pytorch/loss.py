# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/02_loss.ipynb.

# %% auto 0
__all__ = ['MlvlPointGenerator', 'SamplingResult', 'YOLOXLoss']

# %% ../nbs/02_loss.ipynb 3
from typing import Any, Type, List, Optional, Callable, Tuple, Union
from functools import partial

# %% ../nbs/02_loss.ipynb 4
import numpy as np

import torch
import torch.nn.functional as F
import torchvision

# %% ../nbs/02_loss.ipynb 5
from .utils import multi_apply
from .simota import SimOTAAssigner

# %% ../nbs/02_loss.ipynb 7
class MlvlPointGenerator:
    """Standard points generator for multi-level (Mlvl) feature maps in 2D points-based detectors.
    
    Based on OpenMMLab's implementation in the mmdetection library:
    
    - [OpenMMLab's Implementation](https://github.com/open-mmlab/mmdetection/blob/d64e719172335fa3d7a757a2a3636bd19e9efb62/mmdet/core/anchor/point_generator.py#L44)
    
    """

    def __init__(self, 
                 strides:List[Union[int, Tuple[int, int]]], # Strides of anchors in multiple feature levels in order (w, h).
                 offset:float = 0.5 # The offset of points, the value is normalized with corresponding stride.
                ):
        self.strides = strides
        self.offset = offset

    @property
    def num_levels(self) -> int:
        """int: number of feature levels that the generator will be applied."""
        return len(self.strides)

    def grid_priors(self, featmap_sizes: List[Tuple[int, int]], with_stride: bool = False) -> List[torch.Tensor]:
        """Generate grid points of multiple feature levels.

        Args:
            featmap_sizes (List[Tuple[int, int]]): List of feature map sizes in multiple feature levels, each size arrange as as (h, w).
            with_stride (bool): Whether to concatenate the stride to the last dimension of points.

        Return:
            list[torch.Tensor]: Points of multiple feature levels.
        """
        assert self.num_levels == len(featmap_sizes)
        multi_level_priors = [self.single_level_grid_priors(featmap_sizes[i], i, with_stride) for i in range(self.num_levels)]
        return multi_level_priors

    def single_level_grid_priors(self,
                                 featmap_size: Tuple[int, int],
                                 level_idx: int,
                                 with_stride: bool = False) -> torch.Tensor:
        """Generate grid Points of a single level.

        Args:
            featmap_size (Tuple[int, int]): Size of the feature maps, arrange as (h, w).
            level_idx (int): The index of corresponding feature map level.
            with_stride (bool): Concatenate the stride to the last dimension of points.

        Return:
            torch.Tensor: Points of single feature levels.
        """
        feat_h, feat_w = featmap_size
        stride = self.strides[level_idx]
        shift_x = (torch.arange(0., feat_w) + self.offset) * stride
        shift_y = (torch.arange(0., feat_h) + self.offset) * stride
        shift_xx, shift_yy = torch.meshgrid(shift_x, shift_y, indexing='xy')
        shift_xx, shift_yy = shift_xx.flatten(), shift_yy.flatten()

        if with_stride:
            shifts = torch.stack([shift_xx, shift_yy, torch.full_like(shift_xx, stride), torch.full_like(shift_yy, stride)], dim=-1)
        else:
            shifts = torch.stack([shift_xx, shift_yy], dim=-1)
        
        return shifts

# %% ../nbs/02_loss.ipynb 9
class SamplingResult:
    """
    Bounding box sampling result.
    
    Based on OpenMMLab's implementation in the mmdetection library:
    
    - [OpenMMLab's Implementation](https://github.com/open-mmlab/mmdetection/blob/d64e719172335fa3d7a757a2a3636bd19e9efb62/mmdet/core/bbox/samplers/sampling_result.py#L7)

    """
    def __init__(self, 
                 positive_indices:np.array, # Indices of the positive samples.
                 negative_indices:np.array, # Indices of the negative samples.
                 bboxes:np.array, # Array containing all bounding boxes.
                 ground_truth_bboxes:torch.Tensor, # Tensor containing all ground truth bounding boxes.
                 assignment_result, # Object that contains the ground truth indices and labels corresponding to each sample.
                 ground_truth_flags:np.array # Array indicating which samples are ground truth.
                ):
        # Indices of positive and negative samples
        self.positive_indices = positive_indices
        self.negative_indices = negative_indices

        # Bounding boxes for positive and negative samples
        self.positive_bboxes = bboxes[positive_indices]
        self.negative_bboxes = bboxes[negative_indices]
        
        # Flag indicating if the positive samples are ground truth
        self.is_positive_ground_truth = ground_truth_flags[positive_indices]

        # Number of ground truths
        self.number_of_ground_truths = ground_truth_bboxes.shape[0]
        
        # Indices of the assigned ground truths for positive samples
        self.positive_assigned_ground_truth_indices = assignment_result.ground_truth_box_indices[positive_indices] - 1

        # Check the consistency of ground truth bounding boxes and assigned indices
        if ground_truth_bboxes.numel() == 0:
            if self.positive_assigned_ground_truth_indices.numel() != 0:
                raise ValueError('Mismatch between ground truth bounding boxes and positive assigned ground truth indices.')
            self.positive_ground_truth_bboxes = torch.empty_like(ground_truth_bboxes).view(-1, 4)
        else:
            if len(ground_truth_bboxes.shape) < 2:
                ground_truth_bboxes = ground_truth_bboxes.view(-1, 4)
            self.positive_ground_truth_bboxes = ground_truth_bboxes[self.positive_assigned_ground_truth_indices, :]

        # If labels are assigned, assign labels for positive samples. Otherwise, set it as None
        if assignment_result.category_labels is not None:
            self.positive_ground_truth_labels = assignment_result.category_labels[positive_indices]
        else:
            self.positive_ground_truth_labels = None

# %% ../nbs/02_loss.ipynb 11
class YOLOXLoss:
    """
    YOLOXLoss class implements the loss function used in the YOLOX model. 
    
    Based on OpenMMLab's implementation in the mmdetection library:
    
    - [OpenMMLab's Implementation](https://github.com/open-mmlab/mmdetection/blob/d64e719172335fa3d7a757a2a3636bd19e9efb62/mmdet/models/dense_heads/yolox_head.py#L321)

    #### Pseudocode
    1. Receive class_scores, predicted_bboxes, objectness_scores, ground_truth_bboxes, and ground_truth_labels as input. These are all tensors.
    2. Calculate the size of the feature maps from class scores.
    3. Create multi-level prior boxes using the feature map sizes.
    4. Reshape and flatten class predictions, bounding box predictions, and objectness scores. 
    5. Combine the class predictions, bounding box predictions, and objectness scores from different levels into one tensor respectively.
    6. Combine the prior boxes from different levels into one tensor and decode the bounding boxes using these prior boxes and bounding box predictions.
    7. For each image, calculate the targets for classification, bounding box, objectness, and optionally, L1 loss.
    8. Determine the total number of positive samples.
    9. Combine the masks, class targets, objectness targets, and bounding box targets from different images into one tensor respectively.
    10. Calculate the bounding box loss, objectness loss, and class loss using the respective targets and predictions.
        - If L1 loss is enabled, calculate the L1 loss using the bounding box predictions and L1 targets.
    11. Return a dictionary containing the classification, bounding box, objectness, and optionally, L1 loss.
    
    
    """
    def __init__(self, 
                 num_classes:int, # The number of target classes.
                 bbox_loss_weight:float=2.0, # The weight for the loss function to calculate the bounding box regression loss.
                 class_loss_weight:float=1.0, # The weight for the loss function to calculate the classification loss.
                 objectness_loss_weight:float=1.0, # The weight for the loss function to calculate the objectness loss.
                 l1_loss_weight:float=1.0, # The weight for the loss function to calculate the L1 loss.
                 use_l1:bool=False, # Whether to use L1 loss in the calculation.
                 strides:List[int]=[8,16,32] # The list of strides.
                ):
        
        self.num_classes = num_classes
        
        giou_loss_partial = partial(torchvision.ops.generalized_box_iou_loss, reduction='none', eps=1e-16)
        self.bbox_loss_func = lambda bx1, bx2 : (1-(1-giou_loss_partial(boxes1=bx1, boxes2=bx2))**2).sum()
        self.class_loss_func = partial(F.binary_cross_entropy_with_logits, reduction='sum')
        self.objectness_loss_func = partial(F.binary_cross_entropy_with_logits, reduction='sum')
        self.l1_loss_func = partial(F.l1_loss, reduction='sum')
        
        
        self.bbox_loss_weight = bbox_loss_weight
        self.class_loss_weight = class_loss_weight
        self.objectness_loss_weight = objectness_loss_weight
        self.l1_loss_weight = l1_loss_weight
        
        
        self.use_l1 = use_l1
        
        # Initialize the prior box generator and assigner
        self.prior_generator = MlvlPointGenerator(strides, offset=0)
        self.assigner = SimOTAAssigner(center_radius=2.5)
        
    def bbox_decode(self, prior_boxes, predicted_boxes):
        """
        Decodes the predicted bounding boxes based on the prior boxes.

        Args:
            prior_boxes (torch.Tensor): The prior bounding boxes.
            predicted_boxes (torch.Tensor): The predicted bounding boxes.

        Returns:
            torch.Tensor: The decoded bounding boxes.
        """
        # Calculate box centroids (geometric centers) and sizes
        box_centroids = (predicted_boxes[..., :2] * prior_boxes[:, 2:]) + prior_boxes[:, :2]
        box_sizes = torch.exp(predicted_boxes[..., 2:]) * prior_boxes[:, 2:]

        # Calculate corners of bounding boxes
        top_left = box_centroids - box_sizes / 2
        bottom_right = box_centroids + box_sizes / 2

        # Stack coordinates to create decoded bounding boxes
        decoded_boxes = torch.cat((top_left, bottom_right), -1)

        return decoded_boxes


    def sample(self, assignment_result, bboxes, ground_truth_boxes, **kwargs):
        """
        Samples positive and negative indices based on the assignment result.
        
        Args:
            assignment_result (Object): The assignment result obtained from assigner.
            bboxes (torch.Tensor): The predicted bounding boxes.
            ground_truth_boxes (torch.Tensor): The ground truth boxes.

        Returns:
            SamplingResult: The sampling result containing positive and negative indices.
        """
        positive_indices = torch.nonzero(
            assignment_result.ground_truth_box_indices > 0, as_tuple=False).squeeze(-1).unique()
        negative_indices = torch.nonzero(
            assignment_result.ground_truth_box_indices == 0, as_tuple=False).squeeze(-1).unique()
        ground_truth_flags = bboxes.new_zeros(bboxes.shape[0], dtype=torch.uint8)
        sampling_result = SamplingResult(positive_indices, negative_indices, bboxes, ground_truth_boxes,
                                         assignment_result, ground_truth_flags)
        return sampling_result


    def get_l1_target(self, l1_target, ground_truth_boxes, prior_boxes, epsilon=1e-8):
        """
        Calculates the L1 target.

        Args:
            l1_target (torch.Tensor): The L1 target tensor.
            ground_truth_boxes (torch.Tensor): The ground truth boxes.
            prior_boxes (torch.Tensor): The prior bounding boxes.
            epsilon (float, optional): A small value to prevent division by zero. Defaults to 1e-8.

        Returns:
            torch.Tensor: The updated L1 target.
        """
        ground_truth_centroid_and_wh = torchvision.ops.box_convert(ground_truth_boxes, 'xyxy', 'cxcywh')
        l1_target[:, :2] = (ground_truth_centroid_and_wh[:, :2] - prior_boxes[:, :2]) / prior_boxes[:, 2:]
        l1_target[:, 2:] = torch.log(ground_truth_centroid_and_wh[:, 2:] / prior_boxes[:, 2:] + epsilon)
        return l1_target


    def get_target_single(self, class_preds, objectness_score, prior_boxes, decoded_bboxes,
                           ground_truth_bboxes, ground_truth_labels):
        """
        Calculates the targets for a single image.

        Args:
            class_preds (torch.Tensor): The predicted class probabilities.
            objectness_score (torch.Tensor): The predicted objectness scores.
            prior_boxes (torch.Tensor): The prior bounding boxes.
            decoded_bboxes (torch.Tensor): The decoded bounding boxes.
            ground_truth_bboxes (torch.Tensor): The ground truth boxes.
            ground_truth_labels (torch.Tensor): The ground truth labels.

        Returns:
            Tuple: The targets for classification, objectness, bounding boxes, and L1 (if applicable), along with
            the foreground mask and the number of positive samples.
        """
        # Get the number of prior boxes and ground truth labels
        num_prior_boxes = prior_boxes.size(0)
        num_ground_truths = ground_truth_labels.size(0)

        # Match dtype of ground truth bounding boxes to the dtype of decoded bounding boxes
        ground_truth_bboxes = ground_truth_bboxes.to(decoded_bboxes.dtype)

        # Check if there are no ground truth labels (objects) in the image
        if num_ground_truths == 0:
            # Initialize targets as zero tensors, and foreground_mask as a boolean tensor with False values
            class_targets = class_preds.new_zeros((0, self.num_classes))
            bbox_targets = class_preds.new_zeros((0, 4))
            l1_targets = class_preds.new_zeros((0, 4))
            objectness_targets = class_preds.new_zeros((num_prior_boxes, 1))
            foreground_mask = class_preds.new_zeros(num_prior_boxes).bool()
            return (foreground_mask, class_targets, objectness_targets, bbox_targets,
                    l1_targets, 0)  # Return zero for num_positive_per_image

        # Calculate the offset for the prior boxes
        offset_prior_boxes = torch.cat([prior_boxes[:, :2] + prior_boxes[:, 2:] * 0.5, prior_boxes[:, 2:]], dim=-1)

        # Assign ground truth objects to prior boxes and get assignment results
        assignment_result = self.assigner.assign(
            class_preds.sigmoid() * objectness_score.unsqueeze(1).sigmoid(),
            offset_prior_boxes, decoded_bboxes, ground_truth_bboxes, ground_truth_labels)

        # Use assignment results to sample prior boxes
        sampling_result = self.sample(assignment_result, prior_boxes, ground_truth_bboxes)
        
        # Get the indices of positive (object-containing) samples
        positive_indices = sampling_result.positive_indices
        num_positive_per_image = positive_indices.size(0)

        # Get the maximum IoU values for the positive samples
        positive_ious = assignment_result.max_iou_values[positive_indices]

        # Generate class targets
        class_targets = F.one_hot(sampling_result.positive_ground_truth_labels, self.num_classes) * positive_ious.unsqueeze(-1)

        # Initialize objectness targets as zeros and set the values at positive_indices to 1
        objectness_targets = torch.zeros_like(objectness_score).unsqueeze(-1)
        objectness_targets[positive_indices] = 1

        # Generate bounding box targets
        bbox_targets = sampling_result.positive_ground_truth_bboxes

        # Initialize L1 targets as zeros
        l1_targets = class_preds.new_zeros((num_positive_per_image, 4))

        # If use_l1 is True, calculate L1 targets
        if self.use_l1:
            l1_targets = self.get_l1_target(l1_targets, bbox_targets, prior_boxes[positive_indices])

        # Initialize foreground_mask as zeros and set the values at positive_indices to True
        foreground_mask = torch.zeros_like(objectness_score).to(torch.bool)
        foreground_mask[positive_indices] = 1

        # Return the computed targets, the foreground mask, and the number of positive samples per image
        return (foreground_mask, class_targets, objectness_targets, bbox_targets, l1_targets, num_positive_per_image)
    
    
    def flatten_and_concat(self, tensors, num_images, reshape_dims=None):
        new_shape = (num_images, -1, reshape_dims) if reshape_dims else (num_images, -1)
        return torch.cat([t.permute(0, 2, 3, 1).reshape(*new_shape) for t in tensors], dim=1)

    
    def __call__(self, num_images, class_scores, predicted_bboxes, objectness_scores, ground_truth_bboxes, ground_truth_labels):
        """
        Main method to compute the YOLOX loss.

        Args:
            num_images (int): The number of images in a batch.
            class_scores (List[torch.Tensor]): A list of class scores for each scale.
            predicted_bboxes (List[torch.Tensor]): A list of predicted bounding boxes for each scale.
            objectness_scores (List[torch.Tensor]): A list of objectness scores for each scale.
            ground_truth_bboxes (List[torch.Tensor]): A list of ground truth bounding boxes for each image.
            ground_truth_labels (List[torch.Tensor]): A list of ground truth labels for each image.

        Returns:
            Dict: A dictionary with the classification, bounding box, objectness, and optionally, L1 loss.
        """
        # Compute feature map sizes from class scores
        feature_map_sizes = [class_score.shape[2:] for class_score in class_scores]

        # Generate multi-level priors
        multilevel_prior_boxes = self.prior_generator.grid_priors(
            feature_map_sizes, with_stride=True).to(class_scores[0].device)
        
        # Flatten and concatenate class predictions, bounding box predictions, and objectness scores
        flatten_class_preds = self.flatten_and_concat(class_scores, num_images, self.num_classes)
        flatten_bbox_preds = self.flatten_and_concat(predicted_bboxes, num_images, 4)
        flatten_objectness_scores = self.flatten_and_concat(objectness_scores, num_images)
                    
        # Concatenate and decode prior boxes
        flatten_prior_boxes = torch.cat(multilevel_prior_boxes)
        flatten_decoded_bboxes = self.bbox_decode(flatten_prior_boxes, flatten_bbox_preds)

        # Compute targets
        (positive_masks, class_targets, objectness_targets, bbox_targets, l1_targets,
         num_positive_images) = multi_apply(
             self.get_target_single, flatten_class_preds.detach(),
             flatten_objectness_scores.detach(),
             flatten_prior_boxes.unsqueeze(0).repeat(num_images, 1, 1),
             flatten_decoded_bboxes.detach(), ground_truth_bboxes, ground_truth_labels)

        # Calculate total number of samples
        num_total_samples = max(sum(num_positive_images), 1)

        # Concatenate all positive masks, class targets, objectness targets, and bounding box targets
        positive_masks = torch.cat(positive_masks, 0)
        class_targets = torch.cat(class_targets, 0)
        objectness_targets = torch.cat(objectness_targets, 0)
        bbox_targets = torch.cat(bbox_targets, 0)

        # If use_l1 is True, concatenate l1 targets
        if self.use_l1:
            l1_targets = torch.cat(l1_targets, 0)

        # Compute bounding box loss
        loss_bbox = self.bbox_loss_func(
            flatten_decoded_bboxes.view(-1, 4)[positive_masks],
            bbox_targets) / num_total_samples

        # Compute objectness loss
        loss_obj = self.objectness_loss_func(flatten_objectness_scores.view(-1, 1),
                                 objectness_targets) / num_total_samples

        # Compute class loss
        loss_cls = self.class_loss_func(
            flatten_class_preds.view(-1, self.num_classes)[positive_masks],
            class_targets) / num_total_samples
                
        # Scale losses
        loss_bbox *= self.bbox_loss_weight
        loss_obj *= self.objectness_loss_weight
        loss_cls *= self.class_loss_weight
        

        # Initialize loss dictionary
        loss_dict = dict(loss_cls=loss_cls, loss_bbox=loss_bbox, loss_obj=loss_obj)

        # If use_l1 is True, compute L1 loss and add it to the loss dictionary
        if self.use_l1:
            loss_l1 = self.l1_loss_func(
                flatten_bbox_preds.view(-1, 4)[positive_masks],
                l1_targets) / num_total_samples
            loss_l1 *= self.l1_loss_weight
            loss_dict.update(loss_l1=loss_l1)

        # Return loss dictionary
        return loss_dict
