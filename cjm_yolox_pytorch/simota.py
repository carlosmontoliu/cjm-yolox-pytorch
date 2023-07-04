# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/03_simota.ipynb.

# %% auto 0
__all__ = ['AssignResult', 'SimOTAAssigner']

# %% ../nbs/03_simota.ipynb 4
from typing import Any, Type, List, Optional, Callable, Tuple, Union
from functools import partial

# %% ../nbs/03_simota.ipynb 5
import torch
import torch.nn.functional as F
import torchvision

# %% ../nbs/03_simota.ipynb 7
class AssignResult():
    """
    Stores assignments between predicted bounding boxes and actual truth bounding boxes.
    
    Based on OpenMMLab's implementation in the mmdetection library:
    
    - [OpenMMLab's Implementation](https://github.com/open-mmlab/mmdetection/blob/d64e719172335fa3d7a757a2a3636bd19e9efb62/mmdet/core/bbox/assigners/assign_result.py#L7)

    """

    def __init__(self, 
                 num_ground_truth_boxes:int, # The number of actual truth boxes considered when computing this assignment
                 ground_truth_box_indices:torch.LongTensor, # For each predicted bounding box, this indicates the 1-based index of the assigned actual truth box. 0 means unassigned and -1 means ignore.
                 max_iou_values:torch.FloatTensor, # The Intersection over Union (IoU) between the predicted bounding box and its assigned actual truth box.
                 category_labels:torch.LongTensor=None # If specified, for each predicted bounding box, this indicates the category label of the assigned actual truth box.
                ):
        self.num_ground_truth_boxes = num_ground_truth_boxes
        self.ground_truth_box_indices = ground_truth_box_indices
        self.max_iou_values = max_iou_values
        self.category_labels = category_labels

# %% ../nbs/03_simota.ipynb 9
class SimOTAAssigner():
    """
    Computes matching between predictions and ground truth.
    
    Based on OpenMMLab's implementation in the mmdetection library:
    
    - [OpenMMLab's Implementation](https://github.com/open-mmlab/mmdetection/blob/d64e719172335fa3d7a757a2a3636bd19e9efb62/mmdet/core/bbox/assigners/sim_ota_assigner.py#L14)
    
    
    #### Pseudocode

    1. Receive as input: predicted_scores, priors, decoded_bounding_boxes, ground_truth_bounding_boxes, and ground_truth_labels. These are all tensors.
    2. Initialize a large value for HIGH_COST_VALUE.
    3. Calculate the number of ground truth bounding boxes and predicted bounding boxes.
    4. Create a tensor `assigned_gt_inds` with the same size as the number of predicted bounding boxes and fill it with zeros. 
    5. If there are no ground truth bounding boxes or predicted bounding boxes, return an `AssignResult` with no assignments.
    6. If there are no ground truth bounding boxes, assign all predicted bounding boxes to the background.
    7. Calculate which priors are within a ground truth bounding box and which priors are in the center of a ground truth bounding box.
    8. Extract the valid decoded bounding boxes and valid predicted scores, i.e., those that are inside a ground truth bounding box and at the center.
    9. Compute the IoU between valid bounding boxes and ground truth bounding boxes. 
        - Calculate the IoU cost by taking the negative logarithm of this IoU.
    10. Convert the ground truth labels to one-hot format and calculate the classification cost using the valid predicted scores and one-hot ground truth labels.
    11. Calculate the total cost matrix by adding up the classification cost, the IoU cost and, if not within a ground truth bounding box and at the center, a high cost.
    12. Use the dynamic_k_matching method on the cost matrix to perform a dynamic matching between the ground truth bounding boxes and valid bounding boxes.
        - Obtain the matched ground truth indices and IoU scores.
    13. Update the `assigned_gt_inds` with the matched ground truth indices.
    14. Create a tensor `assigned_labels` of the same size as `assigned_gt_inds`, and fill it with -1.
        - Update `assigned_labels` with the labels of the matched ground truth boxes.
    15. Create a tensor `max_overlaps` of the same size as `assigned_gt_inds`, and fill it with -HIGH_COST_VALUE.
        - Update `max_overlaps` with the IoU scores of the matched ground truth boxes.
    16. Return an instance of `AssignResult` with the number of ground truth boxes, the `assigned_gt_inds`, `max_overlaps`, and `assigned_labels`.
    
    """

    def __init__(self,
                 center_radius:float=2.5, # Ground truth center size to judge whether a prior is in center.
                 candidate_topk:int=10, # The candidate top-k which used to get top-k ious to calculate dynamic-k.
                 iou_weight:float=3.0, # The scale factor for regression iou cost.
                 cls_weight:float=1.0 # The scale factor for classification cost.
                ):
        self.center_radius = center_radius
        self.candidate_topk = candidate_topk
        self.iou_weight = iou_weight
        self.cls_weight = cls_weight

    def assign(self,
           pred_scores,
           priors,
           decoded_bboxes,
           gt_bboxes,
           gt_labels,
           gt_bboxes_ignore=None,
           eps=1e-7):
        """Assign ground truth to priors using SimOTA (Similarity-Overlap-Training-Assignment).

        This method finds the best assignment of predicted bounding boxes (priors) to 
        the ground truth bounding boxes (gt) based on a combination of classification and 
        regression (IoU) costs.

        Args:
            pred_scores (Tensor): Classification scores of each prior box across all classes. 
                It is a 2D-Tensor with shape [num_priors, num_classes].
            priors (Tensor): Prior bounding boxes of one image in format [cx, xy, stride_w, stride_y].
                It is a 2D-Tensor with shape [num_priors, 4].
            decoded_bboxes (Tensor): Predicted bounding boxes of one image in format [tl_x, tl_y, br_x, br_y].
                It is a 2D-Tensor with shape [num_priors, 4].
            gt_bboxes (Tensor): Ground truth bounding boxes of one image in format [tl_x, tl_y, br_x, br_y].
                It is a 2D-Tensor with shape [num_gts, 4].
            gt_labels (Tensor): Ground truth labels of one image, 
                It is a Tensor with shape [num_gts].
            gt_bboxes_ignore (Tensor, optional): Ground truth bounding boxes that are
                labelled as `ignored`, e.g., crowd boxes in COCO.
            eps (float): A value added to the denominator for numerical
                stability. Default 1e-7.

        Returns:
            :obj:`AssignResult`: The assigned result. This includes information about the index 
                of the ground truth box each prediction is assigned to, the IoU between the 
                predictions and their assigned ground truth, and the category labels for each prediction.
        """
        HIGH_COST_VALUE = 100000000
        num_gt = gt_bboxes.size(0)
        num_bboxes = decoded_bboxes.size(0)

        # assign 0 by default
        assigned_gt_inds = decoded_bboxes.new_full((num_bboxes, ), 0, dtype=torch.long)
        if num_gt == 0 or num_bboxes == 0:
            # No ground truth or boxes, return empty assignment
            max_overlaps = decoded_bboxes.new_zeros((num_bboxes, ))
            if num_gt == 0:
                # No truth, assign everything to background
                assigned_gt_inds[:] = 0
            if gt_labels is None:
                assigned_labels = None
            else:
                assigned_labels = decoded_bboxes.new_full((num_bboxes, ), -1, dtype=torch.long)
            return AssignResult(num_gt, assigned_gt_inds, max_overlaps, category_labels=assigned_labels)

        # Get info whether a prior is in gt bounding box and also the center of gt bounding box
        valid_mask, is_in_boxes_and_center = self.get_in_gt_and_in_center_info(priors, gt_bboxes)

        # Extract valid bounding boxes and scores (i.e., those in ground truth boxes and centers)
        valid_decoded_bbox = decoded_bboxes[valid_mask]
        valid_pred_scores = pred_scores[valid_mask]
        num_valid = valid_decoded_bbox.size(0)

        # Compute IoU between valid decoded bounding boxes and gt bounding boxes
        pairwise_ious = torchvision.ops.generalized_box_iou(valid_decoded_bbox, gt_bboxes)
        # Compute IoU cost
        iou_cost = -torch.log(pairwise_ious + eps)

        # Convert gt_labels to one-hot format and calculate classification cost
        gt_onehot_label = F.one_hot(gt_labels.to(torch.int64), pred_scores.shape[-1]).float().unsqueeze(0).repeat(num_valid, 1, 1)
        valid_pred_scores = valid_pred_scores.unsqueeze(1).repeat(1, num_gt, 1)
        cls_cost = F.binary_cross_entropy(valid_pred_scores.sqrt_(), gt_onehot_label, reduction='none').sum(-1)

        # Calculate total cost matrix by combining classification and IoU costs, 
        # and assign a high cost (HIGH_COST_VALUE) for bboxes not in both boxes and centers
        cost_matrix = cls_cost * self.cls_weight + iou_cost * self.iou_weight + (~is_in_boxes_and_center) * HIGH_COST_VALUE

        # Perform matching between ground truth and valid bounding boxes based on the cost matrix
        matched_pred_ious, matched_gt_inds = self.dynamic_k_matching(cost_matrix, pairwise_ious, num_gt, valid_mask)

        # Convert to AssignResult format: assign matched gt indices, labels and IoU scores
        assigned_gt_inds[valid_mask] = matched_gt_inds + 1
        assigned_labels = assigned_gt_inds.new_full((num_bboxes, ), -1)
        assigned_labels[valid_mask] = gt_labels[matched_gt_inds].long()
        max_overlaps = assigned_gt_inds.new_full((num_bboxes, ), -HIGH_COST_VALUE, dtype=torch.float32)
        max_overlaps[valid_mask] = matched_pred_ious
        return AssignResult(num_gt, assigned_gt_inds, max_overlaps, category_labels=assigned_labels)

    def get_in_gt_and_in_center_info(self, priors, gt_bboxes):
        """Get the information about whether priors are in ground truth boxes or center.

        Args:
            priors (Tensor): All priors of one image, a 2D-Tensor with shape [num_priors, 4]
                in [cx, xy, stride_w, stride_y] format.
            gt_bboxes (Tensor): Ground truth bboxes of one image, a 2D-Tensor
                with shape [num_gts, 4] in [tl_x, tl_y, br_x, br_y] format.

        Returns:
            Tuple[Tensor, Tensor]: The first tensor indicates if the prior is in any ground truth box or center, 
            the second tensor specifies if the prior is in both the ground truth box and center.
        """

        # Repeat the prior values across the new dimension to facilitate the calculations
        repeated_x = priors[:, 0, None]
        repeated_y = priors[:, 1, None]
        repeated_stride_x = priors[:, 2, None]
        repeated_stride_y = priors[:, 3, None]

        # Calculate deltas (distances from priors to the boundaries of the ground truth boxes)
        deltas = torch.stack([
            repeated_x - gt_bboxes[:, 0], 
            repeated_y - gt_bboxes[:, 1], 
            gt_bboxes[:, 2] - repeated_x, 
            gt_bboxes[:, 3] - repeated_y], dim=1)

        # Check if any value of deltas is positive, which means the prior is within the ground truth box
        is_in_gts = deltas.min(dim=1).values > 0
        # Check if a prior is in any ground truth box
        is_in_gts_all = is_in_gts.any(dim=1)

        # Calculate the centers of the ground truth boxes
        gt_cxs = (gt_bboxes[:, 0] + gt_bboxes[:, 2]) / 2.0
        gt_cys = (gt_bboxes[:, 1] + gt_bboxes[:, 3]) / 2.0

        # Calculate deltas for center boxes (distances from priors to the boundaries of the center boxes)
        ct_deltas = torch.stack([
            repeated_x - (gt_cxs - self.center_radius * repeated_stride_x),
            repeated_y - (gt_cys - self.center_radius * repeated_stride_y),
            (gt_cxs + self.center_radius * repeated_stride_x) - repeated_x,
            (gt_cys + self.center_radius * repeated_stride_y) - repeated_y], dim=1)

        # Check if any value of ct_deltas is positive, which means the prior is within the center box
        is_in_cts = ct_deltas.min(dim=1).values > 0
        # Check if a prior is in any center box
        is_in_cts_all = is_in_cts.any(dim=1)

        # Check if a prior is in either any ground truth box or any center box
        is_in_gts_or_centers = is_in_gts_all | is_in_cts_all
        # Check if a prior is in both any ground truth box and any center box
        is_in_boxes_and_centers = (is_in_gts[is_in_gts_or_centers, :] & is_in_cts[is_in_gts_or_centers, :])

        return is_in_gts_or_centers, is_in_boxes_and_centers
    
    def dynamic_k_matching(self, cost, pairwise_ious, num_gt, valid_mask):
        """
        This method performs dynamic k-matching. This is a core part of the SimOTA assignment
        where each ground truth object dynamically chooses k bounding box predictions that best 
        match itself according to the cost matrix. Then, if there are any conflicts (i.e., one 
        prediction is selected by multiple ground truths), the conflicts are resolved by choosing 
        the pair with the smallest cost.

        Args:
            cost (Tensor): A 2D tensor representing the cost matrix calculated from both 
                classification cost and regression IoU cost. Shape is [num_priors, num_gts].
            pairwise_ious (Tensor): A 2D tensor representing IoU scores between predictions and 
                ground truths. Shape is [num_priors, num_gts].
            num_gt (int): The number of ground truth boxes.
            valid_mask (Tensor): A 1D tensor representing which predicted boxes are valid based 
                on being in gt bboxes and in centers. Shape is [num_priors].

        Returns:
            matched_pred_ious (Tensor): IoU scores for matched pairs. Shape is [num_priors].
            matched_gt_inds (Tensor): The indices of the ground truth for each prior. Shape is [num_priors].
        """
        # Initialize the matching matrix with zeros
        matching_matrix = torch.zeros_like(cost)

        # Select the top k IoUs for dynamic-k calculation
        topk_ious, _ = torch.topk(pairwise_ious, self.candidate_topk, dim=0)

        # Calculate dynamic k for each ground truth
        dynamic_ks = topk_ious.sum(0).int().clamp(min=1)

        # For each ground truth, find top k matching priors based on smallest cost
        _, pos_idx = cost.topk(k=dynamic_ks.max().item(), dim=0, largest=False)
        for gt_idx in range(num_gt):
            matching_matrix[pos_idx[:dynamic_ks[gt_idx], gt_idx], gt_idx] = 1

        # If a prior matches multiple ground truths, keep only the one with smallest cost
        prior_match_gt_mask = matching_matrix.sum(1) > 1
        if prior_match_gt_mask.any():
            _, cost_argmin = cost[prior_match_gt_mask].min(dim=1)
            matching_matrix[prior_match_gt_mask] *= 0
            matching_matrix[prior_match_gt_mask, cost_argmin] = 1

        # Update the valid mask based on final matches
        valid_mask[valid_mask.clone()] = matching_matrix.sum(1) > 0

        # Get the final matched ground truth indices and IoUs for valid predicted boxes
        fg_mask_inboxes = matching_matrix.sum(1) > 0
        matched_gt_inds = matching_matrix[fg_mask_inboxes].argmax(1)
        matched_pred_ious = (matching_matrix * pairwise_ious).sum(1)[fg_mask_inboxes]

        return matched_pred_ious, matched_gt_inds
