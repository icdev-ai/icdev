def quicksort(arr, left=0, right=None):
    """
    Efficient in-place quicksort with 3-way partitioning (Dutch National Flag algorithm).
    Handles duplicates effectively, ensuring O(n log n) average time complexity.
    Uses O(log n) stack space via recursion.
    """
    if right is None:
        right = len(arr)
    
    if left >= right - 1:
        return
    
    # 3-way partitioning: partition into <pivot, =pivot, >pivot
    pivot = arr[left + (right - left) // 2]
    i = left          # boundary of less-than section
    j = left          # current element being examined
    k = right - 1     # boundary of greater-than section
    
    while j <= k:
        if arr[j] < pivot:
            arr[i], arr[j] = arr[j], arr[i]
            i += 1
            j += 1
        elif arr[j] > pivot:
            arr[j], arr[k] = arr[k], arr[j]
            k -= 1
        else:
            j += 1
    
    # Recursively sort the sections less than and greater than pivot
    # Elements equal to pivot are already in correct position
    quicksort(arr, left, i)
    quicksort(arr, k + 1, right)