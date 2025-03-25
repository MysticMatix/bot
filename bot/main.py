#write to tmp.txt
def write_to_file():
    with open('tmp.txt', 'w') as f:
        f.write('Hello World')

if __name__ == '__main__':
    write_to_file()