import re
from datetime import datetime
import pandas as pd

@staticmethod
def convert_japanese_priority_number(priority_number: str) -> str:
    if priority_number.startswith("JPW"):
        return priority_number
    if priority_number.startswith("JP"):
        match = re.match(r'JP(\d{5}|\d{6})(\d{2})$', priority_number)
        if match:
            main_part = match.group(1).zfill(6)
            year_suffix = match.group(2)
            if int(year_suffix) <= 50:
                year = f'20{year_suffix}'
            else:
                year = f'19{year_suffix}'
            return f'JP{year}{main_part}'

    return priority_number

def sort_orap(row, ccw_to_wo_mapping, data):
    priority_numbers = row['priority_numbers']
    # print("priority_numbers:", priority_numbers)
    app_number_full = row['app_number']

    orap_list = [p for p in priority_numbers if re.match(r'^[A-Z]{2}\w*$', p)]
    # print("orap_list:", orap_list)
    
    orap_date_map = {}
    for num in orap_list:
        if num in ccw_to_wo_mapping:
            wo_number, wo_date = ccw_to_wo_mapping[num]
            orap_date_map[wo_number] = wo_date
        else:
            priority_dates = row['priority_dates']

            # Ensure priority_dates is a dictionary
            if isinstance(priority_dates, tuple):
                # If it's a tuple, convert it to a dictionary
                priority_dates = dict(priority_dates)
            elif not isinstance(priority_dates, dict):
                # If it's neither a tuple nor a dictionary, raise an error or handle it
                print(f"Unexpected type for priority_dates: {type(priority_dates)}")
                return row
            
            orap_date_map[num] = priority_dates.get(num)

    # **Filter out None values before sorting**
    filtered_orap_dates = {k: v for k, v in orap_date_map.items() if v is not None}

    # Handle case where all dates were None
    if not filtered_orap_dates:
        row['orap'] = ''
        row['orap_history'] = []
        return row
        
    sorted_orap = sorted(
        filtered_orap_dates.items(),  # Use filtered dictionary
        key=lambda x: pd.to_datetime(x[1], errors='coerce')
    )
    
    # print("sorted_orap:", sorted_orap)
           
    # Update `row['orap']` iteratively  
    for orap, date in sorted_orap:
        # print("last priority numbers:", priority_numbers[-1])
        # print("app_number_full:", app_number_full)
        if 'W' not in orap:
            # Ensure there are at least two elements in priority_numbers
            if len(priority_numbers) > 1:
                if priority_numbers[-1] == row['app_number']:
                    row['orap'] = priority_numbers[-2]
                else:
                    row['orap'] = priority_numbers[-1]
            elif len(priority_numbers) == 1:
                row['orap'] = priority_numbers[-1]
            else:
                row['orap'] = ''
        else:
            row['orap'] = orap  # Update row's `orap`
        # print(f"Updated row['orap']: {row['orap']}")
        # print()

    # Assign earliest valid ORAP if the above code chunk fails
    # row['orap'] = sorted_orap[0][0]
    
    # Ensure row['orap_history'] is updated
    unique_orap_set = set()
    hist_orap_list = []
    for orap, date in sorted_orap:
        if orap not in unique_orap_set:
            unique_orap_set.add(orap)
            hist_orap_list.append((orap, date))
    row['orap_history'] = hist_orap_list
    # print("hist_orap_list:", hist_orap_list)

    # Return the updated row to reflect changes in the dataframe
    return row
