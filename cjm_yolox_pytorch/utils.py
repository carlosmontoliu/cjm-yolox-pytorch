# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_utils.ipynb.

# %% auto 0
__all__ = ['multi_apply', 'generate_output_grids', 'apply_to_inputs']

# %% ../nbs/01_utils.ipynb 4
from pathlib import Path

from typing import Any, Type, List, Optional, Callable, Tuple
from functools import partial

# %% ../nbs/01_utils.ipynb 5
import torch

# %% ../nbs/01_utils.ipynb 7
def multi_apply(func:Callable[..., Any], # Function to apply.
                *args:Any,
                **kwargs:Any
               ) -> Tuple[List[Any], ...]:
    """
    Applies the function `func` to each set of arguments in `*args`, 
    possibly using keyword arguments `**kwargs`.
    
    Based on OpenMMLab's implementation in the mmdetection library:
    
    - [OpenMMLab's Implementation](https://github.com/open-mmlab/mmdetection/blob/d64e719172335fa3d7a757a2a3636bd19e9efb62/mmdet/core/utils/misc.py#L11)

    """
    try:
        pfunc = partial(func, **kwargs) if kwargs else func
        map_results = map(pfunc, *args)
        return tuple(map(list, zip(*map_results)))
    except Exception as e:
        print(f"Error occurred: {str(e)}")
        return ()

# %% ../nbs/01_utils.ipynb 10
def generate_output_grids(height, width, strides=[8,16,32]):
        """
        Generate a tensor containing grid coordinates and strides for a given height and width.

        Args:
            height (int): The height of the image.
            width (int): The width of the image.

        Returns:
            torch.Tensor: A tensor containing grid coordinates and strides.
        """

        all_coordinates = []

        # We will use a loop but it won't affect the exportability of the model to ONNX 
        # as the loop is not dependent on the input data (height, width) but on the 'strides' which is model parameter.
        for i, stride in enumerate(strides):
            # Calculate the grid height and width
            grid_height = height // stride
            grid_width = width // stride

            # Generate grid coordinates
            g1, g0 = torch.meshgrid(torch.arange(grid_height), torch.arange(grid_width), indexing='ij')
            
            # Create a tensor of strides
            s = torch.full((grid_height, grid_width), stride)

            # Stack the coordinates along with the stride
            coordinates = torch.stack((g0.flatten(), g1.flatten(), s.flatten()), dim=-1)

            # Append to the list
            all_coordinates.append(coordinates)

        # Concatenate all tensors in the list along the first dimension
        output_grids = torch.cat(all_coordinates, dim=0)

        return output_grids

# %% ../nbs/01_utils.ipynb 12
def apply_to_inputs(func, inputs):
    """
    Apply the function `func` to each input in `inputs`, handling errors and returning a list of results.
    """
    results = []
    for input_ in inputs:
        try:
            result = func(*input_)
            results.append(result)
        except Exception as e:
            print(f"Error occurred: {str(e)}")
            results.append(None)  # or raise the exception if you want to stop execution immediately
    return results
