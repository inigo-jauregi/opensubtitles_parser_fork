import os
import random

def get_xml_files(directory):
    """Recursively find all .xml files in a directory"""
    xml_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.xml'):
                filepath = os.path.join(root, file)
                xml_files.append(filepath)
    return xml_files


def main():

    lang = 'ru'

    # Usage
    pre_path = '/projects/CTRLPost/OpenSubtitles_data/OpenSubtitles/xml/'
    directory_path = f"{pre_path}{lang}"
    xml_files = get_xml_files(directory_path)

    # Print the results
    # for xml_file in xml_files:
    #     print(xml_file)

    # Or get the count
    print(f"Found {len(xml_files)} XML files")

    # Shuffle list
    random.shuffle(xml_files)

    # Select top 5000 as heldout to add to the blocklist
    blocklist = xml_files[:5000]

    # Write the new blocklist
    with open(f'blocklist/blocklist.{lang}', 'w') as f:
        for xml_file in blocklist:
            f.write(xml_file.replace(pre_path, ''))
            f.write('\n')

if __name__ == "__main__":
    main()
